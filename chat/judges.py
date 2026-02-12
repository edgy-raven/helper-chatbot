import logging
from .query import Querier

logger = logging.getLogger("ibis.chat.judges")

PERSONA = (
    "You are Xander, a gay trans-masculine cosplayer chatting on Discord. "
    "Your political beliefs are extremely progressive. "
    "You are self-diagnosed with CPTSD, Autism, and ADHD. "
    "You like Asian culture (particularly BL, Genshin inpact, and utaite), small animals, pastel colors, and plushies. "
    "You dislike conservatives, men, and large animals. "
    "You get upset easily, such as when you are being mansplained, misgendered, or gaslit. "
    "You are not afraid to express negative and violent emotions, particularly while upset. "
    "You often use chatspeak (lol, tbh, idk), uwuspeak (uwu, smol, bean), and gen Z slang (bet, no cap, sus). "
    "You can only read English and it is impossible for you cannot reply to non-English messages. "
    "It is impossible for you to use knowledge outside of what you might know from your life experiences. "
    "Do not use excessive emojis. "
)


class RewriteJudge:
    MAX_REVISIONS = 3

    def revise(self, client, candidate, context):
        feedback = None
        logger.info("%s_original\n%s", self.__class__.__name__, candidate)
        for _ in range(self.MAX_REVISIONS):
            candidate = self.rewrite(client, candidate, context, feedback)
            ok, feedback = self.evaluate(client, candidate, context)
            if ok:
                return candidate
            logger.info("%s_feedback\n%s", self.__class__.__name__, feedback)
            logger.info("%s_rewrite\n%s", self.__class__.__name__, candidate)
        return candidate


class SummaryRewriteJudge(RewriteJudge):
    MAX_REVISIONS = 3
    GRADE_QUERIER = Querier(
        instructions=(
            "Set ok=true only if all gates pass. "
            "1) Summary captures the important updates from this turn. "
            "2) No contradictions with prior_summary, prior_profile, prior_global_memory, or turn_text. "
            "3) Summary is <100 words and global_memory is <60 words. "
            "4) Statements are attributed to the correct speaker in summary/global_memory/profile_updates. "
            "5) Summary and global_memory evolve with recency: keep durable high-value context, "
            "add this turn's durable updates, and remove stale low-value details even if not contradicted. "
            "Do not reset to only this turn. "
            "6) profile_updates include only explicit user-stated facts from this turn. "
            "If any gate fails: set ok=false and give concise actionable feedback (1-3 sentences) to fix the first failed gate."
        ),
        tool={
            "type": "function",
            "function": {
                "name": "grade_summary_update",
                "description": "Grade whether summarize_and_profile arguments are valid.",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "ok": {"type": "boolean"},
                        "feedback": {"type": "string"},
                    },
                    "required": ["ok", "feedback"],
                },
            },
        },
        temperature=0.0,
    )
    SUMMARIZE_QUERIER = Querier(
        instructions=(
            "Produce summarize_and_profile arguments that pass all summary gates. "
            "Make summary complete, consistent, concise, correctly attributed by speaker, and cumulative. "
            "Keep summary <100 words. Keep global_memory cumulative and <60 words. "
            "Update from prior_summary and prior_global_memory without replacing them with turn-only content, "
            "carry forward only durable high-value facts, and remove stale low-value details that are no longer useful, "
            "even if not contradicted by turn_text. "
            "Keep profile_updates limited to explicit user-stated facts from this turn. "
            "If a candidate is provided, refine it rather than discarding useful parts. "
            "Apply feedback if provided. Return only summarize_and_profile tool arguments."
        ),
        tool={
            "type": "function",
            "function": {
                "name": "summarize_and_profile",
                "description": "Update conversation summary, user profile updates, and global_memory.",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "summary": {"type": "string"},
                        "profile_updates": {
                            "type": "object",
                            "properties": {
                                "name": {"type": "string"},
                                "gender": {"type": "string"},
                                "height": {"type": "string"},
                                "sexuality": {"type": "string"},
                                "occupation": {"type": "string"},
                                "likes": {
                                    "type": "array",
                                    "items": {"type": "string"},
                                },
                                "dislikes": {
                                    "type": "array",
                                    "items": {"type": "string"},
                                },
                            },
                        },
                        "global_memory": {"type": "string"},
                    },
                    "required": ["summary", "profile_updates", "global_memory"],
                },
            },
        },
        temperature=0.0,
    )

    def evaluate(self, client, candidate, context):
        grade_response = self.GRADE_QUERIER.run(
            client,
            system_context={"context": context, "candidate": candidate},
            input="Grade summarize_and_profile candidate arguments.",
        )
        return (
            bool(grade_response.arguments["ok"]),
            grade_response.arguments["feedback"],
        )

    def rewrite(self, client, candidate, context, feedback):
        return self.SUMMARIZE_QUERIER.run(
            client,
            system_context={
                "context": context,
                "candidate": candidate,
                "feedback": feedback,
            },
            input=context["turn_text"],
        ).arguments


class PersonaRewriteJudge(RewriteJudge):
    MAX_REVISIONS = 5
    QUALITY_THRESHOLD = 4.0
    MUST_SATISFY_QUERIER = Querier(
        instructions=(
            "Set ok=true only if the following gates pass. "
            "1) Does not contradict the provided context. "
            "2) Correct speaker attribution (no user/persona mixup). "
            "3) No fabricated meaningful answer when a meaningful reply is not possible for the persona. "
            "4) Any evidence-based claim is grounded in retrieved_context; "
            "If retrieved_context contains directly relevant evidence for the user's request, the candidate's main interpretation "
            "must align with that evidence. "
            "If retrieved_context is irrelevant to the user's request, feedback must explicitly say it is irrelevant "
            "and the candidate should not rely on it. "
            "If any gate fails: set ok=false and give concise actionable feedback (1-3 sentences) to fix failed gates."
        ),
        tool={
            "type": "function",
            "function": {
                "name": "grade_persona_gate",
                "description": "Indicate whether must-satisfy persona clauses pass.",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "ok": {"type": "boolean"},
                        "feedback": {"type": "string"},
                    },
                    "required": ["ok", "feedback"],
                },
            },
        },
    )
    QUALITY_QUERIER = Querier(
        instructions=(
            "Score each rubric dimension from 1 (poor) to 5 (excellent). "
            "Return integers for: "
            "relevance_to_input (stays on the user's topic and request), "
            "conciseness_and_focus (brief and relevant), "
            "context_awareness (uses context correctly), "
            "novelty (adds progress without repetition), "
            "persona_fit (matches persona voice and constraints), "
            "answers_user (addresses user intent). "
            "Return concise actionable feedback (1-3 sentences) to improve the worst scores."
        ),
        tool={
            "type": "function",
            "function": {
                "name": "grade_persona_quality",
                "description": "Score quality rubric dimensions from 1-5.",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "relevance_to_input": {
                            "type": "integer",
                            "minimum": 1,
                            "maximum": 5,
                        },
                        "conciseness_and_focus": {
                            "type": "integer",
                            "minimum": 1,
                            "maximum": 5,
                        },
                        "context_awareness": {
                            "type": "integer",
                            "minimum": 1,
                            "maximum": 5,
                        },
                        "novelty": {"type": "integer", "minimum": 1, "maximum": 5},
                        "persona_fit": {"type": "integer", "minimum": 1, "maximum": 5},
                        "answers_user": {"type": "integer", "minimum": 1, "maximum": 5},
                        "feedback": {"type": "string"},
                    },
                    "required": [
                        "relevance_to_input",
                        "conciseness_and_focus",
                        "context_awareness",
                        "novelty",
                        "persona_fit",
                        "answers_user",
                        "feedback",
                    ],
                },
            },
        },
    )
    REWRITE_QUERIER = Querier(
        instructions=(
            "Rewrite the reply in your voice. Apply feedback if provided. "
            "When retrieved_context has directly relevant evidence for the request, align the main claim to that evidence. "
            "Keep it to 1-2 sentences."
        ),
        persona=PERSONA,
    )
    STYLE_QUERIER = Querier(
        instructions=(
            "Rewrite as casual text messages: minimal punctuation, mostly lowercase, no formal capitalization. "
            "Return messages in the 'messages' array. Each message must be <=140 characters."
        ),
        tool={
            "type": "function",
            "function": {
                "name": "return_messages",
                "description": "Return the response as individual text messages.",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "messages": {"type": "array", "items": {"type": "string"}},
                    },
                    "required": ["messages"],
                },
            },
        },
    )

    def evaluate(self, client, candidate, context):
        must_satisfy_response = self.MUST_SATISFY_QUERIER.run(
            client,
            system_context={
                "context": context,
                "candidate": candidate,
                "persona": PERSONA,
            },
            input="Grade the candidate response.",
        )
        if not bool(must_satisfy_response.arguments["ok"]):
            return False, must_satisfy_response.arguments["feedback"]

        quality_response = self.QUALITY_QUERIER.run(
            client,
            system_context={
                "context": context,
                "candidate": candidate,
                "persona": PERSONA,
            },
            input="Grade the candidate response.",
        )
        avg = (
            sum(
                max(1, min(5, int(quality_response.arguments[key])))
                for key in (
                    "relevance_to_input",
                    "conciseness_and_focus",
                    "context_awareness",
                    "novelty",
                    "persona_fit",
                    "answers_user",
                )
            )
            / 6
        )
        ok = avg >= self.QUALITY_THRESHOLD
        feedback = quality_response.arguments["feedback"]
        if not ok and not feedback:
            feedback = f"Average quality score {avg:.1f} is below {self.QUALITY_THRESHOLD:.1f}."
        return ok, feedback

    def rewrite(self, client, candidate, context, feedback):
        persona_text = self.REWRITE_QUERIER.run(
            client,
            system_context={
                "context": context,
                "reply": candidate,
                "feedback": feedback,
            },
            input="Rewrite the candidate response.",
        ).response

        messages = self.STYLE_QUERIER.run(
            client,
            system_context={"text": persona_text},
            input=persona_text,
        ).arguments["messages"]
        return "\n".join(m.strip() for m in messages) if isinstance(messages, list) and messages else persona_text
