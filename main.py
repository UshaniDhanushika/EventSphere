
from fastapi import FastAPI, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy.orm import Session

from database import engine, Base, SessionLocal
from models import Event as EventModel

from models import User
from security import (
    hash_password,
    verify_password,
    create_access_token,
    read_access_token
)

from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials
import jwt

# Create database tables
Base.metadata.create_all(bind=engine)

app = FastAPI()


# Pydantic model for validating incoming data
class EventCreate(BaseModel):
    title: str
    category: str
    venue: str
    price: float


class UserRegister(BaseModel):
    username: str
    password: str


class UserLogin(BaseModel):
    username: str
    password: str

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



@app.post("/register")
def register_user(
    user: UserRegister,
    db: Session = Depends(get_db)
):

    existing_user = db.query(User).filter(
        User.username == user.username
    ).first()

    if existing_user:
        raise HTTPException(
            status_code=400,
            detail="Username already exists"
        )

    new_user = User(
        username=user.username,
        password_hash=hash_password(user.password),
        role="attendee"
    )

    db.add(new_user)
    db.commit()
    db.refresh(new_user)

    return {
        "message": "Registration successful",
        "username": new_user.username,
        "role": new_user.role
    }


@app.post("/login")
def login_user(
    user: UserLogin,
    db: Session = Depends(get_db)
):

    existing_user = db.query(User).filter(
        User.username == user.username
    ).first()

    if not existing_user:
        raise HTTPException(
            status_code=401,
            detail="Invalid username or password"
        )

    if not verify_password(
        user.password,
        existing_user.password_hash
    ):
        raise HTTPException(
            status_code=401,
            detail="Invalid username or password"
        )

    token = create_access_token(
        existing_user.username,
        existing_user.role
    )

    return {
        "access_token": token,
        "token_type": "bearer"
    }


bearer_scheme = HTTPBearer()


# Get the currently logged-in user
# Get the currently logged-in user
def get_current_user(
    credentials: HTTPAuthorizationCredentials = Depends(bearer_scheme),
    db: Session = Depends(get_db)
):

    token = credentials.credentials

    try:
        data = read_access_token(token)
        username = data.get("sub")

        if not username:
            raise HTTPException(
                status_code=401,
                detail="Invalid token"
            )

    except jwt.InvalidTokenError as error:
        print("JWT ERROR:", repr(error))

        raise HTTPException(
            status_code=401,
            detail="Invalid or expired token"
        )

    user = db.query(User).filter(
        User.username == username
    ).first()

    if user is None:
        raise HTTPException(
            status_code=401,
            detail="User not found"
        )

    return user
    
# Only allow users with the required role
def require_role(required_role):

    def check_role(
        current_user: User = Depends(get_current_user)
    ):

        if current_user.role != required_role:
            raise HTTPException(
                status_code=403,
                detail="You do not have permission"
            )

        return current_user

    return check_role


# Test login protection
@app.get("/me")
def get_my_profile(
    current_user: User = Depends(get_current_user)
):

    return {
        "username": current_user.username,
        "role": current_user.role
    }


# Admin-only example
@app.get("/admin/check")
def admin_check(
    current_user: User = Depends(require_role("admin"))
):

    return {"message": "Welcome, Admin!"}


# Staff-only example
@app.get("/staff/check")
def staff_check(
    current_user: User = Depends(require_role("staff"))
):

    return {"message": "Welcome, Staff!"}


