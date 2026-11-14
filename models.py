from sqlalchemy import Column, Integer, String, Float

from database import Base


class Event(Base):

    __tablename__ = "events"

    id = Column(Integer, primary_key=True, index=True)

    title = Column(String, nullable=False)

    category = Column(String, nullable=False)

    venue = Column(String, nullable=False)

    price = Column(Float, nullable=False)


from database import engine, Base
import models

Base.metadata.create_all(bind=engine)