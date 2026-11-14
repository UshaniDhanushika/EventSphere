
from fastapi import FastAPI

# Create the FastAPI application
app = FastAPI()

# Home page endpoint
@app.get("/")
def home():
    return {"message": "Welcome to EventSphere"}

# About endpoint
@app.get("/about")
def about():
    return {
        "project": "EventSphere",
        "description": "University Event Booking System"
    }
