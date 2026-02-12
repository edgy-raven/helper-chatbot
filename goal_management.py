import data_models
from chat import register_tool


@register_tool(
    description="Add a task for the user.",
    parameters={
        "type": "object",
        "properties": {
            "task_type": {
                "type": "string",
                "enum": ["goal", "daily", "one_off"],
            },
            "description": {"type": "string"},
            "due_text": {"type": "string"},
        },
        "required": ["task_type", "description"],
    },
)
def add_task(context, task_type, description, due_text=None):
    discord_id = context.discord_id
    with data_models.Session() as session:
        user = session.get(data_models.User, discord_id)
        user.tasks.append(
            data_models.Task(
                task_type=task_type,
                description=description,
                due_text=due_text,
                progress=None,
            )
        )
        session.commit()
        return f"Added {task_type} task: {description}"


@register_tool(
    description="Update progress for a task. If the progress indicates completion, mark the task as completed.",
    parameters={
        "type": "object",
        "properties": {
            "task_id": {"type": "integer"},
            "progress": {"type": "string"},
            "is_task_completed": {"type": "boolean"},
        },
        "required": ["task_id", "progress", "is_task_completed"],
    },
)
def update_progress(context, task_id, progress, is_task_completed):
    discord_id = context.discord_id
    with data_models.Session() as session:
        task = session.get(data_models.Task, task_id)
        if task and task.user_id == discord_id:
            task.progress = progress
            task.completed = is_task_completed
            session.commit()
            suffix = " and marked complete" if is_task_completed else ""
            return f"Updated task {task_id}: {progress}{suffix}"
        return f"Task {task_id} not found for this user."
