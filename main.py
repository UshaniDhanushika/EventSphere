
from fastapi import FastAPI, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy.orm import Session

from database import engine, Base, SessionLocal
from models import Event as EventModel


# Create database tables
Base.metadata.create_all(bind=engine)

app = FastAPI()


# Pydantic model for validating incoming data
class EventCreate(BaseModel):
    title: str
    category: str
    venue: str
    price: float


# Database session dependency
def get_db():
    db = SessionLocal()

    try:
        yield db
    finally:
        db.close()


# Get all events
@app.get("/events")
def get_events(db: Session = Depends(get_db)):

    events = db.query(EventModel).all()

    return events


# Create an event
@app.post("/events")
def create_event(
    event: EventCreate,
    db: Session = Depends(get_db)
):

    new_event = EventModel(
        title=event.title,
        category=event.category,
        venue=event.venue,
        price=event.price
    )

    db.add(new_event)
    db.commit()
    db.refresh(new_event)

    return {
        "message": "Event created successfully",
        "event": new_event
    }


# Update an event
@app.put("/events/{event_id}")
def update_event(
    event_id: int,
    updated_event: EventCreate,
    db: Session = Depends(get_db)
):

    event = db.query(EventModel).filter(
        EventModel.id == event_id
    ).first()

    if event is None:
        raise HTTPException(
            status_code=404,
            detail="Event not found"
        )

    event.title = updated_event.title
    event.category = updated_event.category
    event.venue = updated_event.venue
    event.price = updated_event.price

    db.commit()
    db.refresh(event)

    return {
        "message": "Event updated successfully",
        "event": event
    }


# Delete an event
@app.delete("/events/{event_id}")
def delete_event(
    event_id: int,
    db: Session = Depends(get_db)
):

    event = db.query(EventModel).filter(
        EventModel.id == event_id
    ).first()

    if event is None:
        raise HTTPException(
            status_code=404,
            detail="Event not found"
        )

    db.delete(event)
    db.commit()

    return {
        "message": "Event deleted successfully"
    }
