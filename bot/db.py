import enum
from datetime import UTC, datetime

from sqlalchemy import (
    BigInteger,
    Boolean,
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

from bot.timeutils import LEGACY_REPEATS

# Готовые категории для нового пользователя (их можно удалить и создать свои)
DEFAULT_CATEGORIES = {
    "work": "💼 Работа",
    "home": "🏠 Дом",
    "health": "🩺 Здоровье",
    "study": "📚 Учёба",
    "people": "👥 Встречи",
    "fun": "🎉 Отдых",
}


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
    # За сколько минут напоминать по умолчанию при быстром добавлении
    default_remind_min: Mapped[int] = mapped_column(Integer, default=15, server_default="15")
    # Время утренней сводки «ЧЧ:ММ» в поясе пользователя (None — выключена)
    summary_time: Mapped[str | None] = mapped_column(String(5), nullable=True)
    # Готовые категории уже добавлены (их можно удалить, повторно не добавляем)
    categories_seeded: Mapped[bool] = mapped_column(Boolean, default=False, server_default="0")
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow)


class Category(Base):
    """Категория событий, которую пользователь создал сам (или одна из готовых)."""

    __tablename__ = "categories"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), index=True)
    name: Mapped[str] = mapped_column(String(40))  # «💼 Работа», «Собака»
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow)


class Event(Base):
    __tablename__ = "events"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), index=True)
    title: Mapped[str] = mapped_column(String(500))
    starts_at: Mapped[datetime] = mapped_column(UTCDateTime, index=True)
    # Конец промежутка «14:20–19:30» (None — у события только время начала)
    ends_at: Mapped[datetime | None] = mapped_column(UTCDateTime, nullable=True)
    remind_before_min: Mapped[int] = mapped_column(Integer, default=15)
    status: Mapped[EventStatus] = mapped_column(
        Enum(EventStatus, values_callable=lambda e: [m.value for m in e]),
        default=EventStatus.ACTIVE,
    )
    # Повтор: «1d», «3d», «2w», «1m», «wd» — см. bot.timeutils (None — разовое событие)
    repeat: Mapped[str | None] = mapped_column(String(10), nullable=True)
    # Первое повторение серии: от него считаются все остальные (и число месяца)
    repeat_anchor: Mapped[datetime | None] = mapped_column(UTCDateTime, nullable=True)
    # Категория (None — без категории, событие только в общем списке)
    category_id: Mapped[int | None] = mapped_column(
        ForeignKey("categories.id", ondelete="SET NULL"), nullable=True, index=True
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

    if "repeat" not in columns:
        conn.execute(text("ALTER TABLE events ADD COLUMN repeat VARCHAR(10)"))
    if "repeat_anchor" not in columns:
        conn.execute(text("ALTER TABLE events ADD COLUMN repeat_anchor DATETIME"))
    if "ends_at" not in columns:
        conn.execute(text("ALTER TABLE events ADD COLUMN ends_at DATETIME"))
    if "category_id" not in columns:
        conn.execute(text("ALTER TABLE events ADD COLUMN category_id INTEGER"))
        if "category" in columns:
            _migrate_category_keys(conn)
    # Старые названия повторов → коды «1d», «1w», «1m», «wd»
    for old, new in LEGACY_REPEATS.items():
        conn.execute(
            text("UPDATE events SET repeat = :new WHERE repeat = :old"), {"old": old, "new": new}
        )

    user_columns = {c["name"] for c in inspect(conn).get_columns("users")}
    if "default_remind_min" not in user_columns:
        conn.execute(
            text("ALTER TABLE users ADD COLUMN default_remind_min INTEGER NOT NULL DEFAULT 15")
        )
    if "summary_time" not in user_columns:
        conn.execute(text("ALTER TABLE users ADD COLUMN summary_time VARCHAR(5)"))
    if "categories_seeded" not in user_columns:
        conn.execute(
            text("ALTER TABLE users ADD COLUMN categories_seeded BOOLEAN NOT NULL DEFAULT 0")
        )


def _migrate_category_keys(conn: Connection) -> None:
    """Категории-ключи первой версии («work», «home»…) → строки в таблице categories."""
    rows = conn.execute(
        text("SELECT DISTINCT user_id, category FROM events WHERE category IS NOT NULL")
    ).all()
    for user_id, key in rows:
        name = DEFAULT_CATEGORIES.get(key)
        if name is None:
            continue
        found = conn.execute(
            text("SELECT id FROM categories WHERE user_id = :u AND name = :n"),
            {"u": user_id, "n": name},
        ).scalar()
        if found is None:
            found = conn.execute(
                text("INSERT INTO categories (user_id, name, created_at) VALUES (:u, :n, :t)"),
                {"u": user_id, "n": name, "t": utcnow().replace(tzinfo=None)},
            ).lastrowid
        conn.execute(
            text("UPDATE events SET category_id = :c WHERE user_id = :u AND category = :k"),
            {"c": found, "u": user_id, "k": key},
        )


async def init_db(engine: AsyncEngine) -> None:
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
        await conn.run_sync(_migrate)
