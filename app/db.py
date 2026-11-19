import os

from dotenv import load_dotenv
from sqlalchemy import JSON, create_engine
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column

load_dotenv()
DATABASE_URL = os.getenv("DATABASE_URL", "sqlite:///./keitaro.db")
engine = create_engine(DATABASE_URL)


class Base(DeclarativeBase):
    pass


class CampaignSetup(Base):
    __tablename__ = "campaign_setup"

    alias: Mapped[str] = mapped_column(primary_key=True)
    remote_id: Mapped[int | None]
    phase: Mapped[str]


class FlowDraft(Base):
    __tablename__ = "flow_draft"

    flow_id: Mapped[int] = mapped_column(primary_key=True, autoincrement=False)
    published: Mapped[list[dict]] = mapped_column(JSON)
    draft: Mapped[list[dict] | None] = mapped_column(JSON(none_as_null=True))
