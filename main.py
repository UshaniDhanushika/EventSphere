
from fastapi import FastAPI
from pydantic import BaseModel

app = FastAPI()


# Pydantic model: defines the information an event needs
class Event(BaseModel):
    title: str
    category: str
    venue: str
    price: float


# Temporary storage for our events
events = []


# GET: View all events
@app.get("/events")
def get_events():
    return events


# POST: Create a new event
@app.post("/events")
def create_event(event: Event):
    events.append(event)
    return {
        "message": "Event created successfully",
        "event": event
    }


# PUT: Update an existing event
@app.put("/events/{event_id}")
def update_event(event_id: int, updated_event: Event):

    if event_id < 0 or event_id >= len(events):
        return {"message": "Event not found"}

    events[event_id] = updated_event

    return {
        "message": "Event updated successfully",
        "event": updated_event
    }


# DELETE: Delete an event
@app.delete("/events/{event_id}")
def delete_event(event_id: int):

    if event_id < 0 or event_id >= len(events):
        return {"message": "Event not found"}

    deleted_event = events.pop(event_id)

    return {
        "message": "Event deleted successfully",
        "event": deleted_event
    }
