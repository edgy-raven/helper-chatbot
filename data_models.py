from datetime import datetime, timezone

from sqlalchemy import (
    BigInteger,
    Boolean,
    Column,
    DateTime,
    Enum,
    ForeignKey,
    Integer,
    String,
    Text,
    create_engine,
    text,
)
from sqlalchemy import inspect
from sqlalchemy.orm import declarative_base, relationship, sessionmaker

Base = declarative_base()
engine = None
Session = None


class GlobalMemory(Base):
    __tablename__ = "global_memory"

    key = Column(String, primary_key=True)
    content = Column(Text, default="", nullable=False)


class UserLike(Base):
    __tablename__ = "user_likes"

    user_id = Column(BigInteger, ForeignKey("users.discord_id"), primary_key=True)
    value = Column(String, primary_key=True, nullable=False)


class UserDislike(Base):
    __tablename__ = "user_dislikes"

    user_id = Column(BigInteger, ForeignKey("users.discord_id"), primary_key=True)
    value = Column(String, primary_key=True, nullable=False)


class User(Base):
    __tablename__ = "users"

    discord_id = Column(BigInteger, primary_key=True)
    name = Column(String, nullable=False)
    gender = Column(String, default="", nullable=False)
    height = Column(String, default="", nullable=False)
    sexuality = Column(String, default="", nullable=False)
    occupation = Column(String, default="", nullable=False)
    conversation_summary = Column(Text, default="", nullable=False)

    tasks = relationship("Task", cascade="all, delete-orphan")
    like_items = relationship("UserLike", cascade="all, delete-orphan", lazy="joined")
    dislike_items = relationship("UserDislike", cascade="all, delete-orphan", lazy="joined")

    @classmethod
    def ensure_user(cls, discord_user, session=None):
        if session is None:
            with Session() as owned_session:
                user = cls(
                    discord_id=discord_user.id,
                    name=discord_user.display_name,
                )
                user = owned_session.merge(user)
                owned_session.commit()
                return user.discord_id
        user = cls(
            discord_id=discord_user.id,
            name=discord_user.display_name,
        )
        user = session.merge(user)
        session.commit()
        return user.discord_id

    def to_jsonable(self):
        likes_list = [item.value for item in self.like_items]
        dislikes_list = [item.value for item in self.dislike_items]
        return {
            "discord_id": self.discord_id,
            "profile": {
                "name": self.name,
                "likes": likes_list,
                "dislikes": dislikes_list,
                "gender": self.gender,
                "height": self.height,
                "sexuality": self.sexuality,
                "occupation": self.occupation,
            },
            "conversation_summary": self.conversation_summary,
            "tasks": [task.to_dict() for task in self.tasks if not task.completed],
        }

    def update_profile(self, profile):
        for key in ["name", "gender", "height", "sexuality", "occupation"]:
            if key in profile:
                setattr(self, key, profile[key])

        if "likes" in profile:
            self.like_items.clear()
            likes_val = profile["likes"] or []
            for val in likes_val:
                self.like_items.append(UserLike(user_id=self.discord_id, value=val))

        if "dislikes" in profile:
            self.dislike_items.clear()
            dislikes_val = profile["dislikes"] or []
            for val in dislikes_val:
                self.dislike_items.append(UserDislike(user_id=self.discord_id, value=val))


class Task(Base):
    __tablename__ = "tasks"

    task_id = Column(Integer, primary_key=True)
    task_type = Column(Enum("goal", "daily", "one_off", name="task_type"), nullable=False)
    description = Column(String, nullable=False)
    due_text = Column(String, nullable=True)  # fuzzy timing or None
    progress = Column(String, nullable=True)  # user-entered progress notes
    completed = Column(Boolean, default=False, nullable=False)
    created_at = Column(DateTime, default=lambda: datetime.now(timezone.utc), nullable=False)
    user_id = Column(BigInteger, ForeignKey("users.discord_id"), nullable=True)

    def to_dict(self):
        return {
            "task_id": self.task_id,
            "task_type": self.task_type,
            "description": self.description,
            "due_text": self.due_text,
            "progress": self.progress,
            "completed": self.completed,
        }


def initialize_connection(db_url: str):
    global engine, Session
    engine = create_engine(db_url)
    Base.metadata.create_all(engine)
    Session = sessionmaker(bind=engine)
