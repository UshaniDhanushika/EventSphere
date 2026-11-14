from sqlalchemy import Column, Integer, String, Float

from database import Base



class Event(Base):

    __tablename__ = "events"

    id = Column(Integer, primary_key=True, index=True)

    title = Column(String, nullable=False)

    category = Column(String, nullable=False)

    venue = Column(String, nullable=False)

    price = Column(Float, nullable=False)



class User(Base):

    __tablename__ = "users"

    id = Column(Integer, primary_key=True, index=True)

    username = Column(String, unique=True, nullable=False)

    password_hash = Column(String, nullable=False)

    role = Column(String, default="attendee", nullable=False)
