"""
One-time script to:
1. Change all 'admin' role users to 'agent'
2. Create 4 manager accounts (one per category)

Run: python setup_managers.py
"""
import sys
sys.path.insert(0, '.')

from app import app, db
from models import User, Category, AdminCategories

MANAGER_PASSWORD = "Manager@123"

MANAGERS = [
    {"name": "Software Manager", "email": "rajeevkulkarni1111+software@gmail.com", "category": "software"},
    {"name": "Hardware Manager", "email": "rajeevkulkarni1111+hardware@gmail.com", "category": "hardware"},
    {"name": "Network Manager",  "email": "rajeevkulkarni1111+network@gmail.com",  "category": "network"},
    {"name": "Database Manager", "email": "rajeevkulkarni1111+database@gmail.com", "category": "database"},
]

with app.app_context():
    # Step 1: Change admin -> agent
    admins = User.query.filter_by(role='admin').all()
    for u in admins:
        print(f"  Changed: {u.name} ({u.email}) admin -> agent")
        u.role = 'agent'
    db.session.commit()
    print(f"\nStep 1 done: {len(admins)} users changed from admin to agent.\n")

    # Step 2: Create managers
    created = 0
    for m in MANAGERS:
        if User.query.filter_by(email=m["email"]).first():
            print(f"  SKIP: {m['email']} already exists")
            continue

        user = User(email=m["email"], name=m["name"], role="manager", verified=True)
        user.set_password(MANAGER_PASSWORD)
        db.session.add(user)
        db.session.commit()

        cat = Category.query.filter_by(name=m["category"]).first()
        if cat:
            db.session.add(AdminCategories(admin_id=user.id, category_id=cat.id))
            db.session.commit()
            print(f"  CREATED: {m['name']} -> {m['category']} (id={user.id})")
            created += 1
        else:
            print(f"  WARNING: Category '{m['category']}' not found for {m['name']}")

    print(f"\nStep 2 done: {created} managers created.")
    print(f"\nAll managers use password: {MANAGER_PASSWORD}")
    print("Change these after first login!\n")
