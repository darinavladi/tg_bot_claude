import enum
from datetime import UTC, datetime

from sqlalchemy import (
    BigInteger,
    Connection,
    DateTime,
    Enum,
    ForeignKey,
    Integer,
    String,
    TypeDecorator,
    inspect,
    text,
)
from sqlalchemy.ext.asyncio import AsyncEngine, async_sessionmaker, create_async_engine
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


class UTCDateTime(TypeDecorator):
    """Хранит время в UTC и всегда возвращает его с tzinfo=UTC.

    SQLite не хранит часовой пояс, поэтому приводим всё к UTC при записи
    и возвращаем «осведомлённое» время при чтении.
    """

    impl = DateTime
    cache_ok = True

    def process_bind_param(self, value, dialect):
        if value is None:
            return None
        if value.tzinfo is None:
            raise ValueError("Время должно быть с часовым поясом (aware datetime)")
        return value.astimezone(UTC).replace(tzinfo=None)

    def process_result_value(self, value, dialect):
        if value is None:
            return None
        return value.replace(tzinfo=UTC)


def utcnow() -> datetime:
    return datetime.now(UTC)


class Base(DeclarativeBase):
    pass


class EventStatus(enum.StrEnum):
    ACTIVE = "active"
    DONE = "done"
    CANCELLED = "cancelled"


class User(Base):
    __tablename__ = "users"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)  # Telegram user id
    timezone: Mapped[str] = mapped_column(String(64))
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow)


class Event(Base):
    __tablename__ = "events"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), index=True)
    title: Mapped[str] = mapped_column(String(500))
    starts_at: Mapped[datetime] = mapped_column(UTCDateTime, index=True)
    remind_before_min: Mapped[int] = mapped_column(Integer, default=15)
    status: Mapped[EventStatus] = mapped_column(
        Enum(EventStatus, values_callable=lambda e: [m.value for m in e]),
        default=EventStatus.ACTIVE,
    )
    # Когда прислать следующее напоминание (None — ничего не запланировано)
    remind_at: Mapped[datetime | None] = mapped_column(UTCDateTime, nullable=True, index=True)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow)


def make_engine(url: str) -> AsyncEngine:
    return create_async_engine(url)


def make_sessionmaker(engine: AsyncEngine) -> async_sessionmaker:
    return async_sessionmaker(engine, expire_on_commit=False)


def _migrate(conn: Connection) -> None:
    """Добавляет колонки, появившиеся после первой версии, в уже существующую базу."""
    columns = {c["name"] for c in inspect(conn).get_columns("events")}
    if "remind_at" not in columns:
        conn.execute(text("ALTER TABLE events ADD COLUMN remind_at DATETIME"))
        conn.execute(text("CREATE INDEX IF NOT EXISTS ix_events_remind_at ON events (remind_at)"))
        # Для уже сохранённых будущих событий назначаем напоминание по их настройке
        conn.execute(
            text(
                "UPDATE events SET remind_at = datetime(starts_at, '-' || remind_before_min "
                "|| ' minutes') WHERE status = 'active' AND starts_at > :now"
            ),
            {"now": utcnow().replace(tzinfo=None)},
        )


async def init_db(engine: AsyncEngine) -> None:
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
        await conn.run_sync(_migrate)
