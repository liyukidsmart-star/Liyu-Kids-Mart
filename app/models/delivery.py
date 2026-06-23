import enum
from datetime import datetime, timezone
from app.extensions import db


class DeliveryStatus(enum.Enum):
    assigned = 'assigned'
    picked_up = 'picked_up'
    in_transit = 'in_transit'
    delivered = 'delivered'
    failed = 'failed'


class Driver(db.Model):
    __tablename__ = 'drivers'

    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey('users.id'), nullable=False, unique=True)
    vehicle_type = db.Column(db.String(50), default='Motorcycle')
    license_plate = db.Column(db.String(20), nullable=True)
    is_available = db.Column(db.Boolean, default=True)
    is_active = db.Column(db.Boolean, default=True)
    current_lat = db.Column(db.Float, nullable=True)
    current_lng = db.Column(db.Float, nullable=True)
    rating = db.Column(db.Float, default=5.0)
    total_deliveries = db.Column(db.Integer, default=0)
    created_at = db.Column(db.DateTime, default=lambda: datetime.now(timezone.utc))

    user = db.relationship('User', backref=db.backref('driver_profile', uselist=False))
    deliveries = db.relationship('Delivery', back_populates='driver', lazy='dynamic')

    def to_dict(self):
        return {
            'id': self.id,
            'user_id': self.user_id,
            'name': self.user.full_name if self.user else '',
            'phone': self.user.phone if self.user else '',
            'vehicle_type': self.vehicle_type,
            'license_plate': self.license_plate,
            'is_available': self.is_available,
            'rating': self.rating,
            'total_deliveries': self.total_deliveries,
            'current_lat': self.current_lat,
            'current_lng': self.current_lng,
        }


class Delivery(db.Model):
    __tablename__ = 'deliveries'

    id = db.Column(db.Integer, primary_key=True)
    order_id = db.Column(db.Integer, db.ForeignKey('orders.id'), nullable=False, unique=True)
    driver_id = db.Column(db.Integer, db.ForeignKey('drivers.id'), nullable=True)
    status = db.Column(db.Enum(DeliveryStatus), default=DeliveryStatus.assigned)
    pickup_address = db.Column(db.Text, nullable=True)
    delivery_address = db.Column(db.Text, nullable=True)
    delivery_lat = db.Column(db.Float, nullable=True)
    delivery_lng = db.Column(db.Float, nullable=True)
    estimated_minutes = db.Column(db.Integer, nullable=True)
    actual_minutes = db.Column(db.Integer, nullable=True)
    proof_photo_url = db.Column(db.String(512), nullable=True)
    notes = db.Column(db.Text, nullable=True)
    assigned_at = db.Column(db.DateTime, nullable=True)
    picked_up_at = db.Column(db.DateTime, nullable=True)
    delivered_at = db.Column(db.DateTime, nullable=True)
    created_at = db.Column(db.DateTime, default=lambda: datetime.now(timezone.utc))

    order = db.relationship('Order', back_populates='delivery')
    driver = db.relationship('Driver', back_populates='deliveries')

    def to_dict(self):
        return {
            'id': self.id,
            'order_id': self.order_id,
            'driver': self.driver.to_dict() if self.driver else None,
            'status': self.status.value,
            'delivery_address': self.delivery_address,
            'delivery_lat': self.delivery_lat,
            'delivery_lng': self.delivery_lng,
            'estimated_minutes': self.estimated_minutes,
            'proof_photo_url': self.proof_photo_url,
            'assigned_at': self.assigned_at.isoformat() if self.assigned_at else None,
            'picked_up_at': self.picked_up_at.isoformat() if self.picked_up_at else None,
            'delivered_at': self.delivered_at.isoformat() if self.delivered_at else None,
        }
