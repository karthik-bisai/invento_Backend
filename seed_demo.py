"""Standalone Demonstration & Test Data Seeding Script.

Generates structured, relationally consistent sample data for presentations and testing:
- Multiple isolated organizations (demonstrating multi-tenancy).
- Administrator, Inventory Manager, and Warehouse Staff accounts with realistic roles.
- Pending employee access requests with review notifications.
- Realistic warehouses, product catalogs, and inventory levels.
- Varied inventory conditions: in-stock, low-stock, and critical-stock.
- In-app notification inbox records.

CRITICAL: Never executed automatically. Run manually when demo data is needed.
Usage:
    cd backend
    .venv\\Scripts\\python seed_demo.py [--size small|medium|large] [--reset] [--currency USD|INR|EUR|GBP|AED|SAR|CAD|AUD]
"""

import argparse
import asyncio
import os
import sys
import uuid
from decimal import Decimal
from datetime import datetime, timezone, timedelta

# Ensure backend root is on sys.path
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.core.database import SessionLocal
from app.core.security import hash_password
from app.models.access_request import AccessRequest, RequestStatus
from app.models.inventory import InventoryItem
from app.models.notification import Notification
from app.models.organization import Organization
from app.models.product import Product
from app.models.user import Role, User
from app.models.warehouse import Warehouse


DEFAULT_DEMO_PASSWORD = "DemoPassword123!"

DEMO_ORGS = [
    {
        "name": "Acme Logistics Global",
        "org_code": "ORG-ACME2026",
        "location": "Chicago Central Distribution Hub",
        "currency": "USD",
    },
    {
        "name": "Apex Distribution Ltd",
        "org_code": "ORG-APEX7788",
        "location": "Berlin European Depot",
        "currency": "EUR",
    },
]

DEMO_USERS = [
    {
        "email": "admin@acme.demo",
        "full_name": "Alex Vance",
        "role": Role.ADMIN,
        "job_title": "Director of Supply Chain",
        "department": "Executive Operations",
        "phone": "+1 312 555 0101",
        "org_code": "ORG-ACME2026",
        "is_active": True,
        "email_verified": True,
    },
    {
        "email": "manager@acme.demo",
        "full_name": "Morgan Chen",
        "role": Role.INVENTORY_MANAGER,
        "job_title": "Inventory Operations Lead",
        "department": "Logistics & Fulfillment",
        "phone": "+1 312 555 0102",
        "org_code": "ORG-ACME2026",
        "is_active": True,
        "email_verified": True,
    },
    {
        "email": "staff@acme.demo",
        "full_name": "Sam Rivera",
        "role": Role.WAREHOUSE_STAFF,
        "job_title": "Senior Warehouse Associate",
        "department": "Inbound & Floor Staging",
        "phone": "+1 312 555 0103",
        "org_code": "ORG-ACME2026",
        "is_active": True,
        "email_verified": True,
    },
    {
        "email": "pending@acme.demo",
        "full_name": "Jordan Lee",
        "role": Role.WAREHOUSE_STAFF,
        "job_title": "Warehouse Floor Clerk",
        "department": "Receiving",
        "phone": "+1 312 555 0104",
        "org_code": "ORG-ACME2026",
        "is_active": False,
        "email_verified": True,
        "is_pending_request": True,
    },
    {
        "email": "admin@apex.demo",
        "full_name": "Klaus Weber",
        "role": Role.ADMIN,
        "job_title": "Operations Vice President",
        "department": "Executive",
        "phone": "+49 30 555 0201",
        "org_code": "ORG-APEX7788",
        "is_active": True,
        "email_verified": True,
    },
    {
        "email": "staff@apex.demo",
        "full_name": "Lukas Schmidt",
        "role": Role.WAREHOUSE_STAFF,
        "job_title": "Floor Inventory Specialist",
        "department": "Fulfillment",
        "phone": "+49 30 555 0202",
        "org_code": "ORG-APEX7788",
        "is_active": True,
        "email_verified": True,
    },
]

WAREHOUSES_POOL = [
    {"name": "Warehouse Alpha (Central)", "location": "Chicago, IL - Dock 4A"},
    {"name": "Warehouse Bravo (East Coast)", "location": "Newark, NJ - Portway Bay"},
    {"name": "Warehouse Charlie (West Coast)", "location": "Ontario, CA - Building 2"},
    {"name": "Warehouse Delta (Gulf Logistics)", "location": "Houston, TX - Gateway Terminal"},
    {"name": "Warehouse Echo (Midwest Reserve)", "location": "Indianapolis, IN - Facility B"},
]

PRODUCTS_POOL = [
    {"sku": "BR-2040", "name": "Ball Bearing 6204-2RS Deep Groove", "category": "Bearings", "unit_price": Decimal("4.25"), "reorder_level": 150},
    {"sku": "BR-3050", "name": "Tapered Roller Bearing 30205", "category": "Bearings", "unit_price": Decimal("8.90"), "reorder_level": 80},
    {"sku": "EL-1020", "name": "Terminal Block 10-Pole Rail-Mount", "category": "Electrical", "unit_price": Decimal("2.10"), "reorder_level": 250},
    {"sku": "EL-3100", "name": "Industrial Contactor 24V 3-Pole 32A", "category": "Electrical", "unit_price": Decimal("28.50"), "reorder_level": 40},
    {"sku": "EL-5400", "name": "Solid State Relay 40A 240V AC", "category": "Electrical", "unit_price": Decimal("19.75"), "reorder_level": 30},
    {"sku": "FS-1180", "name": "Hex Cap Screw M8 x 40 Grade 8.8", "category": "Fasteners", "unit_price": Decimal("0.35"), "reorder_level": 500},
    {"sku": "FS-2240", "name": "Socket Head Cap Screw M10 x 50", "category": "Fasteners", "unit_price": Decimal("0.75"), "reorder_level": 400},
    {"sku": "FS-3120", "name": "Nylon Insert Lock Nut M8 Galvanized", "category": "Fasteners", "unit_price": Decimal("0.18"), "reorder_level": 1000},
    {"sku": "HY-2100", "name": "Hydraulic Quick Connect Coupler 3/8", "category": "Hydraulics", "unit_price": Decimal("14.60"), "reorder_level": 60},
    {"sku": "HY-5520", "name": "Reinforced Hydraulic Hose 1/2 in 2-Wire", "category": "Hydraulics", "unit_price": Decimal("32.00"), "reorder_level": 40},
    {"sku": "HY-8800", "name": "Inline Flow Control Valve 1/4 NPT", "category": "Hydraulics", "unit_price": Decimal("22.40"), "reorder_level": 25},
    {"sku": "PK-1002", "name": "Heavy Duty Stretch Film 18in x 1500ft", "category": "Packaging", "unit_price": Decimal("16.80"), "reorder_level": 120},
    {"sku": "PK-4050", "name": "Reinforced Kraft Packing Tape 3in", "category": "Packaging", "unit_price": Decimal("6.20"), "reorder_level": 180},
    {"sku": "PK-9001", "name": "Double-Wall Corrugated Carton 40x40x40", "category": "Packaging", "unit_price": Decimal("3.45"), "reorder_level": 800},
    {"sku": "SF-1010", "name": "Polycarbonate Safety Glasses Clear UV", "category": "Safety Gear", "unit_price": Decimal("4.99"), "reorder_level": 100},
    {"sku": "SF-2020", "name": "Nitrile Dipped Grip Gloves (Size L)", "category": "Safety Gear", "unit_price": Decimal("2.85"), "reorder_level": 250},
    {"sku": "SF-4400", "name": "High-Visibility Safety Vest ANSI Class 2", "category": "Safety Gear", "unit_price": Decimal("8.50"), "reorder_level": 60},
    {"sku": "HD-1100", "name": "Industrial Caster Wheel 6in Swivel Brake", "category": "Hardware", "unit_price": Decimal("18.25"), "reorder_level": 50},
    {"sku": "HD-3300", "name": "Stainless Steel Compression Spring Pack", "category": "Hardware", "unit_price": Decimal("11.40"), "reorder_level": 75},
    {"sku": "TL-5001", "name": "Digital Vernier Caliper 150mm Stainless", "category": "Tools", "unit_price": Decimal("34.50"), "reorder_level": 20},
    {"sku": "TL-6200", "name": "Ergonomic Hand Pallet Strapping Tool", "category": "Tools", "unit_price": Decimal("48.00"), "reorder_level": 15},
    {"sku": "EL-9001", "name": "Miniature Circuit Breaker 16A 1P DIN", "category": "Electrical", "unit_price": Decimal("7.80"), "reorder_level": 90},
    {"sku": "BR-4100", "name": "Pillow Block Mounted Bearing Unit 25mm", "category": "Bearings", "unit_price": Decimal("15.20"), "reorder_level": 45},
    {"sku": "PK-8002", "name": "Anti-Static Bubble Wrap Roll 24in x 250ft", "category": "Packaging", "unit_price": Decimal("27.50"), "reorder_level": 35},
]


def check_production_safety() -> None:
    """Safeguard against seeding against production databases."""
    db_url = settings.DATABASE_URL.lower()
    for forbidden in ["prod", "production", "live", "rds.amazonaws.com"]:
        if forbidden in db_url:
            print(f"[FATAL] Safety abort: DATABASE_URL appears to point to production ('{forbidden}' matched in URL).")
            print("Demo seeder is restricted to development or dedicated test/demo environments.")
            sys.exit(1)


async def reset_demo_data(db: AsyncSession) -> None:
    """Clean up previously seeded demo records by known emails and organization codes."""
    print("-> Cleaning up existing demo data...")
    demo_emails = [u["email"] for u in DEMO_USERS]
    demo_codes = [o["org_code"] for o in DEMO_ORGS]

    res = await db.execute(select(Organization).where(Organization.org_code.in_(demo_codes)))
    orgs = res.scalars().all()
    org_ids = [o.id for o in orgs]

    if org_ids:
        wh_ids_res = await db.execute(select(Warehouse.id).where(Warehouse.organization_id.in_(org_ids)))
        wh_ids = wh_ids_res.scalars().all()
        if wh_ids:
            await db.execute(delete(InventoryItem).where(InventoryItem.warehouse_id.in_(wh_ids)))

        await db.execute(delete(Notification).where(Notification.organization_id.in_(org_ids)))
        await db.execute(delete(AccessRequest).where(AccessRequest.organization_id.in_(org_ids)))
        await db.execute(delete(Product).where(Product.organization_id.in_(org_ids)))
        await db.execute(delete(Warehouse).where(Warehouse.organization_id.in_(org_ids)))
        await db.execute(delete(User).where((User.organization_id.in_(org_ids)) | (User.email.in_(demo_emails))))

        for o in orgs:
            await db.delete(o)
    else:
        await db.execute(delete(User).where(User.email.in_(demo_emails)))

    await db.commit()
    print("   Existing demo data cleared.")


async def seed_data(size: str = "medium", reset: bool = False, currency: str | None = None) -> None:
    check_production_safety()

    limits = {
        "small": {"warehouses": 2, "products": 8},
        "medium": {"warehouses": 3, "products": 16},
        "large": {"warehouses": 5, "products": len(PRODUCTS_POOL)},
    }
    cfg = limits.get(size, limits["medium"])

    print(f"\n=======================================================")
    print(f" Invento Demo Data Seeder")
    print(f" Size: {size.upper()} ({cfg['warehouses']} warehouses, {cfg['products']} products)")
    print(f" Database: {settings.DATABASE_URL.split('@')[-1] if '@' in settings.DATABASE_URL else 'configured DB'}")
    print(f"=======================================================\n")

    async with SessionLocal() as db:
        if reset:
            await reset_demo_data(db)

        # 1. Organizations
        print("1. Creating Organizations...")
        org_map: dict[str, Organization] = {}
        for org_data in DEMO_ORGS:
            existing = await db.scalar(select(Organization).where(Organization.org_code == org_data["org_code"]))
            if existing:
                org_map[org_data["org_code"]] = existing
                print(f"   Using existing organization: {existing.name} ({existing.org_code})")
            else:
                org_curr = currency if (currency and org_data["org_code"] == "ORG-ACME2026") else org_data["currency"]
                org = Organization(
                    id=uuid.uuid4(),
                    org_code=org_data["org_code"],
                    name=org_data["name"],
                    location=org_data["location"],
                    currency=org_curr,
                )
                db.add(org)
                await db.flush()
                org_map[org_data["org_code"]] = org
                print(f"   + Created organization: {org.name} [{org.org_code}] (Currency: {org.currency})")

        # 2. Users
        print("\n2. Creating Users & Access Requests...")
        user_map: dict[str, User] = {}
        hashed_pwd = hash_password(DEFAULT_DEMO_PASSWORD)

        for u_data in DEMO_USERS:
            existing_user = await db.scalar(select(User).where(User.email == u_data["email"]))
            org = org_map[u_data["org_code"]]
            if existing_user:
                user_map[u_data["email"]] = existing_user
                role_label = existing_user.role.value if hasattr(existing_user.role, "value") else existing_user.role
                print(f"   Using existing user: {existing_user.email} [{role_label}]")
            else:
                user = User(
                    id=uuid.uuid4(),
                    email=u_data["email"],
                    hashed_password=hashed_pwd,
                    role=u_data["role"].value if hasattr(u_data["role"], "value") else u_data["role"],
                    full_name=u_data["full_name"],
                    job_title=u_data["job_title"],
                    department=u_data["department"],
                    phone=u_data["phone"],
                    organization_id=org.id,
                    is_active=u_data["is_active"],
                    email_verified=u_data["email_verified"],
                )
                db.add(user)
                await db.flush()
                user_map[u_data["email"]] = user
                role_label = user.role.value if hasattr(user.role, "value") else user.role
                print(f"   + Created user: {user.full_name} <{user.email}> [{role_label}]")

                # If pending approval request
                if u_data.get("is_pending_request"):
                    req = AccessRequest(
                        id=uuid.uuid4(),
                        organization_id=org.id,
                        user_id=user.id,
                        status=RequestStatus.PENDING,
                        note="New warehouse floor employee registered and awaiting department assignment.",
                    )
                    db.add(req)
                    await db.flush()
                    print(f"     + Created pending access approval request for {user.email}")

        # 3. Warehouses for Primary Org
        print("\n3. Creating Warehouses...")
        acme_org = org_map["ORG-ACME2026"]
        warehouses: list[Warehouse] = []
        for w_data in WAREHOUSES_POOL[: cfg["warehouses"]]:
            existing_wh = await db.scalar(
                select(Warehouse).where(
                    Warehouse.organization_id == acme_org.id,
                    Warehouse.name == w_data["name"]
                )
            )
            if existing_wh:
                warehouses.append(existing_wh)
            else:
                wh = Warehouse(
                    id=uuid.uuid4(),
                    organization_id=acme_org.id,
                    name=w_data["name"],
                    location=w_data["location"],
                )
                db.add(wh)
                await db.flush()
                warehouses.append(wh)
                print(f"   + Created warehouse: {wh.name} ({wh.location})")

        # 4. Products for Primary Org
        print("\n4. Creating Catalog Products...")
        products: list[Product] = []
        for p_data in PRODUCTS_POOL[: cfg["products"]]:
            existing_prod = await db.scalar(
                select(Product).where(
                    Product.organization_id == acme_org.id,
                    Product.sku == p_data["sku"]
                )
            )
            if existing_prod:
                products.append(existing_prod)
            else:
                prod = Product(
                    id=uuid.uuid4(),
                    organization_id=acme_org.id,
                    sku=p_data["sku"],
                    name=p_data["name"],
                    category=p_data["category"],
                    unit_price=p_data["unit_price"],
                    reorder_level=p_data["reorder_level"],
                )
                db.add(prod)
                await db.flush()
                products.append(prod)
                print(f"   + Product: [{prod.sku}] {prod.name} - Price: {prod.unit_price} (Reorder: {prod.reorder_level})")

        # 5. Inventory Records across Warehouses
        print("\n5. Stocking Warehouses with Realistic Quantities...")
        inv_count = 0
        now = datetime.now(timezone.utc)

        for i, prod in enumerate(products):
            # Rotate stock scenarios: normal, low-stock, critical-stock
            scenario_mod = i % 5
            for j, wh in enumerate(warehouses):
                existing_item = await db.scalar(
                    select(InventoryItem).where(
                        InventoryItem.product_id == prod.id,
                        InventoryItem.warehouse_id == wh.id,
                    )
                )
                if existing_item:
                    continue

                if scenario_mod == 0:
                    # Critical stock (0 or near 0)
                    on_hand = int(prod.reorder_level * 0.1) if j == 0 else 0
                    reserved = 0
                elif scenario_mod == 1:
                    # Low stock (between 10% and 80% of reorder)
                    on_hand = max(int(prod.reorder_level * 0.4), 5)
                    reserved = min(int(on_hand * 0.3), on_hand)
                elif scenario_mod == 2:
                    # Healthy stock
                    on_hand = int(prod.reorder_level * (1.8 + j * 0.5))
                    reserved = int(on_hand * 0.15)
                else:
                    # Generous stock
                    on_hand = int(prod.reorder_level * (2.5 + j * 0.8))
                    reserved = int(on_hand * 0.08)

                item = InventoryItem(
                    id=uuid.uuid4(),
                    product_id=prod.id,
                    warehouse_id=wh.id,
                    quantity_on_hand=on_hand,
                    reserved_quantity=reserved,
                )
                db.add(item)
                inv_count += 1

        print(f"   + Seeded {inv_count} inventory location records.")

        # 6. Notifications for Acme Admin
        print("\n6. Seeding Notification Inbox Records...")
        acme_admin = user_map.get("admin@acme.demo")
        if acme_admin:
            # Access request alert
            note_req = Notification(
                id=uuid.uuid4(),
                organization_id=acme_org.id,
                user_id=acme_admin.id,
                kind="access_requested",
                title="New Employee Access Request",
                body="Jordan Lee registered for Acme Logistics Global and is awaiting approval.",
                link="/users?tab=requests",
                read_at=None,
                created_at=now - timedelta(minutes=15),
            )
            db.add(note_req)

            # Low stock notification
            low_prod = products[0]
            note_low = Notification(
                id=uuid.uuid4(),
                organization_id=acme_org.id,
                user_id=acme_admin.id,
                kind="low_stock",
                title=f"Low Stock Alert: {low_prod.sku}",
                body=f"{low_prod.name} has fallen below its reorder threshold of {low_prod.reorder_level} units.",
                link="/inventory?low=1",
                read_at=None,
                created_at=now - timedelta(hours=2),
            )
            db.add(note_low)

            # Welcome notification (read)
            note_welcome = Notification(
                id=uuid.uuid4(),
                organization_id=acme_org.id,
                user_id=acme_admin.id,
                kind="system",
                title="Workspace Initialized",
                body="Your organization workspace has been provisioned with tenant isolation and configured settings.",
                link="/settings",
                read_at=now - timedelta(days=1),
                created_at=now - timedelta(days=1),
            )
            db.add(note_welcome)
            print("   + Seeded 3 notification records for Alex Vance (admin).")

        await db.commit()

    print("\n=======================================================")
    print(" DEMO DATA SEEDING COMPLETE!")
    print("=======================================================")
    print("\nDEMO ACCOUNTS READY FOR TESTING:")
    print("-------------------------------------------------------")
    print(f"1. Administrator:")
    print(f"   Email:    admin@acme.demo")
    print(f"   Password: {DEFAULT_DEMO_PASSWORD}")
    print(f"   Org Code: ORG-ACME2026")
    print(f"   Features: Full administration, user creation, currency settings, access approval.")
    print("")
    print(f"2. Inventory Manager:")
    print(f"   Email:    manager@acme.demo")
    print(f"   Password: {DEFAULT_DEMO_PASSWORD}")
    print(f"   Org Code: ORG-ACME2026")
    print(f"   Features: Reorder levels, warehouses, operational reports, inventory adjustments.")
    print("")
    print(f"3. Warehouse Staff:")
    print(f"   Email:    staff@acme.demo")
    print(f"   Password: {DEFAULT_DEMO_PASSWORD}")
    print(f"   Org Code: ORG-ACME2026")
    print(f"   Features: Floor stock counting, on-hand counts, read-only restricted access.")
    print("")
    print(f"4. Multi-Tenant Second Organization Admin (Isolated):")
    print(f"   Email:    admin@apex.demo")
    print(f"   Password: {DEFAULT_DEMO_PASSWORD}")
    print(f"   Org Code: ORG-APEX7788")
    print(f"   Features: Verifies strict tenant isolation (cannot see Acme products or warehouses).")
    print("-------------------------------------------------------\n")


def main() -> None:
    parser = argparse.ArgumentParser(description="Seed Invento with demonstration and test data.")
    parser.add_argument(
        "--size",
        choices=["small", "medium", "large"],
        default="medium",
        help="Volume of sample data to generate (default: medium).",
    )
    parser.add_argument(
        "--reset",
        action="store_true",
        help="Remove previously seeded demo records before seeding fresh data.",
    )
    parser.add_argument(
        "--currency",
        choices=["USD", "INR", "EUR", "GBP", "AED", "SAR", "CAD", "AUD"],
        default="USD",
        help="Initial operational currency for the primary demo organization (default: USD).",
    )
    args = parser.parse_args()
    asyncio.run(seed_data(size=args.size, reset=args.reset, currency=args.currency))


if __name__ == "__main__":
    main()
