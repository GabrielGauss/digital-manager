from sqlalchemy import String, Integer, Float, DateTime, Text, Boolean
from sqlalchemy.orm import Mapped, mapped_column
from datetime import datetime
from database.db import Base


class Bundle(Base):
    __tablename__ = "bundles"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    description: Mapped[str] = mapped_column(Text, nullable=False)
    price: Mapped[float] = mapped_column(Float, nullable=False)
    drive_folder_id: Mapped[str] = mapped_column(String(200), nullable=False)
    drive_folder_url: Mapped[str] = mapped_column(String(500), nullable=False)
    image_count: Mapped[int] = mapped_column(Integer, default=0)
    cover_image_url: Mapped[str] = mapped_column(String(500), nullable=True)

    # ML listing info
    ml_item_id: Mapped[str] = mapped_column(String(100), nullable=True)
    ml_status: Mapped[str] = mapped_column(String(50), default="draft")  # draft | active | paused | closed

    # Metadata
    category: Mapped[str] = mapped_column(String(100), nullable=True)
    tags: Mapped[str] = mapped_column(String(500), nullable=True)  # comma-separated
    active: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)


class Order(Base):
    __tablename__ = "orders"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    ml_order_id: Mapped[str] = mapped_column(String(100), unique=True, nullable=False)
    ml_item_id: Mapped[str] = mapped_column(String(100), nullable=False)
    bundle_id: Mapped[int] = mapped_column(Integer, nullable=True)

    # Buyer info
    buyer_id: Mapped[str] = mapped_column(String(100), nullable=True)
    buyer_nickname: Mapped[str] = mapped_column(String(200), nullable=True)
    buyer_email: Mapped[str] = mapped_column(String(300), nullable=True)

    # Order info
    amount: Mapped[float] = mapped_column(Float, nullable=True)
    status: Mapped[str] = mapped_column(String(50), default="pending")  # pending | paid | delivered | cancelled

    # Delivery
    email_sent: Mapped[bool] = mapped_column(Boolean, default=False)
    email_sent_at: Mapped[datetime] = mapped_column(DateTime, nullable=True)
    drive_link_sent: Mapped[str] = mapped_column(String(500), nullable=True)

    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)


class MLToken(Base):
    __tablename__ = "ml_tokens"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    access_token: Mapped[str] = mapped_column(Text, nullable=False)
    refresh_token: Mapped[str] = mapped_column(Text, nullable=True)
    token_type: Mapped[str] = mapped_column(String(50), default="Bearer")
    expires_at: Mapped[datetime] = mapped_column(DateTime, nullable=True)
    user_id: Mapped[str] = mapped_column(String(100), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)
