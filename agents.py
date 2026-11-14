import uuid


class VenueAgent:
    def __init__(self):
        # The agent's knowledge about available venues
        self.venues = [
            {"name": "Main Hall", "capacity": 500, "location": "Colombo"},
            {"name": "Conference Room", "capacity": 100, "location": "Negombo"},
            {"name": "Open Garden", "capacity": 300, "location": "Colombo"},
            {"name": "Small Meeting Room", "capacity": 30, "location": "Negombo"}
        ]

    def find_venue(self, required_capacity):
        # Find venues that can accommodate the attendees
        suitable_venues = []

        for venue in self.venues:
            if venue["capacity"] >= required_capacity:
                suitable_venues.append(venue)

        # Choose the smallest suitable venue
        if suitable_venues:
            best_venue = min(
                suitable_venues,
                key=lambda venue: venue["capacity"]
            )
            return best_venue

        return None



class BookingAgent:
    def __init__(self):
        # The agent's own booking records
        self.bookings = []

    def create_booking(self, username, event_name, venue):
        # Check that the required information is available
        if not username or not event_name or not venue:
            return "Booking failed: Missing information"

        # Create a new booking
        booking = {
            "username": username,
            "event_name": event_name,
            "venue": venue["name"],
            "status": "confirmed"
        }

        # Store the booking
        self.bookings.append(booking)

        return booking




class TicketAgent:
    def __init__(self):
        self.tickets = []

    def generate_ticket(self, booking):
        # Only confirmed bookings can receive tickets
        if booking["status"] != "confirmed":
            return "Ticket generation failed"

        # Generate a unique ticket ID
        ticket = {
            "ticket_id": str(uuid.uuid4())[:8],
            "username": booking["username"],
            "event_name": booking["event_name"],
            "venue": booking["venue"],
            "status": "valid"
        }

        self.tickets.append(ticket)

        return ticket


    def validate_ticket(self, ticket_id):
        for ticket in self.tickets:
            if ticket["ticket_id"] == ticket_id:
                if ticket["status"] == "valid":
                    return "Ticket is valid"

                return "Ticket has already been used"

        return "Invalid ticket"



class WaitlistAgent:
    def __init__(self):
        self.waitlist = []

    def add_to_waitlist(self, username, event_name):
        # Create a waitlist entry
        entry = {
            "username": username,
            "event_name": event_name,
            "status": "waiting"
        }

        self.waitlist.append(entry)

        return entry

    def notify_next_person(self, event_name):
        # Find the first person waiting for this event
        for person in self.waitlist:
            if (
                person["event_name"] == event_name
                and person["status"] == "waiting"
            ):
                person["status"] = "notified"
                return person

        return None




class NotificationAgent:
    def __init__(self):
        self.notifications = []

    def send_notification(self, username, message):
        # Create a notification
        notification = {
            "username": username,
            "message": message,
            "status": "sent"
        }

        self.notifications.append(notification)

        return notification




class EventCoordinator:
    def __init__(self):
        self.venue_agent = VenueAgent()
        self.booking_agent = BookingAgent()
        self.ticket_agent = TicketAgent()
        self.waitlist_agent = WaitlistAgent()
        self.notification_agent = NotificationAgent()

    def book_event(self, username, event_name, capacity):

        # Step 1: Find a suitable venue
        venue = self.venue_agent.find_venue(capacity)

        if venue is None:
            return "No suitable venue available"

        # Step 2: Create a booking
        booking = self.booking_agent.create_booking(
            username,
            event_name,
            venue
        )

        # Step 3: Generate a ticket
        ticket = self.ticket_agent.generate_ticket(booking)

        # Step 4: Send a notification
        self.notification_agent.send_notification(
            username,
            "Your booking is confirmed. Ticket ID: "
            + ticket["ticket_id"]
        )

        return {
            "booking": booking,
            "ticket": ticket,
            "venue": venue
        }


