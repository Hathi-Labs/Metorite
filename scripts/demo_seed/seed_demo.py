"""Seed a LOCAL Metorite stack with a made-up demo company for screenshots.

The company, "Kite & Co. Interiors", is fictional. Every person, address and
phone number below is invented. Emails use the reserved `.example` domain,
and phone numbers use the clearly fake `+91 98000 000NN` range.

Run it against a fresh, local-only stack (see README.md beside this file):

    uv run python scripts/demo_seed/seed_demo.py

It talks to two things, both on loopback:

* the gateway, over HTTP, for everything the app has a write API for
  (projects, tasks, assignees, comments, personal tasks);
* the database, over SQL, for data that only ever arrives through an
  integration (a mail sync, a WhatsApp webhook, AI triage) and for the
  identity rows the gateway creates at startup.

LOCAL ONLY. It refuses any DSN or gateway URL that is not on 127.0.0.1 or
localhost.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import uuid
from datetime import UTC, date, datetime, timedelta, timezone
from urllib.parse import urlparse

import httpx
import psycopg

DSN = os.environ.get(
    "DEMO_DATABASE_URL", "postgresql://acb:acb@127.0.0.1:5436/acb_tenant"
)
GATEWAY = os.environ.get("DEMO_GATEWAY_URL", "http://127.0.0.1:8011")
TOKEN = os.environ.get("DEMO_GATEWAY_TOKEN", "dev-local-token")

#: The identity the local Next.js app signs in as when no SSO is configured
#: (`DEV_IDENTITY` in workbench/control_plane/src/lib/gateway.ts). The seed
#: cannot change that address, so it gives the member the demo founder's name.
ME = os.environ.get("DEMO_SIGNED_IN_EMAIL", "dev@fracktal.in")

ORG_NAME = "Kite & Co. Interiors"
ORG_DOMAIN = "kiteandco.example"
IST = timezone(timedelta(hours=5, minutes=30))

#: Monday of the demo week. Due dates and calendar blocks sit around it.
WEEK = date(2026, 10, 12)


def anchor_now() -> datetime:
    """The "now" that message times count back from.

    During the working day it is the real time. At night it is 18:30 IST of
    the last working evening, so a screenshot taken late does not show an
    inbox full of messages sent at midnight.
    """
    now = datetime.now(IST)
    if 9 <= now.hour < 21:
        return now.astimezone(UTC)
    evening = now.replace(hour=18, minute=30, second=0, microsecond=0)
    if now.hour < 9:
        evening -= timedelta(days=1)
    return evening.astimezone(UTC)


def d(offset: int) -> date:
    return WEEK + timedelta(days=offset)


def at(offset: int, hh: int, mm: int = 0) -> datetime:
    """A wall-clock time in Bengaluru, `offset` days from the demo Monday."""
    day = d(offset)
    return datetime(day.year, day.month, day.day, hh, mm, tzinfo=IST)


# ── The team ──────────────────────────────────────────────────────────────
# key, name, email, title, department, manager key, phone, skills
TEAM = [
    ("asha", "Asha Rao", ME, "Founder & Principal Designer", "Leadership",
     None, "+91 98000 00011",
     ["Interior design", "Space planning", "Client relations", "Business strategy"]),
    ("rohan", "Rohan Mehta", f"rohan@{ORG_DOMAIN}", "Project Lead", "Projects",
     "asha", "+91 98000 00012",
     ["Project management", "Site coordination", "Budgeting", "AutoCAD"]),
    ("priya", "Priya Nair", f"priya@{ORG_DOMAIN}", "Senior Interior Designer",
     "Design", "asha", "+91 98000 00013",
     ["SketchUp", "3D visualisation", "Material selection", "Lighting design"]),
    ("arjun", "Arjun Pillai", f"arjun@{ORG_DOMAIN}", "Interior Designer",
     "Design", "priya", "+91 98000 00014",
     ["AutoCAD", "Revit", "Furniture design", "V-Ray"]),
    ("karthik", "Karthik Iyer", f"karthik@{ORG_DOMAIN}", "Procurement Manager",
     "Operations", "rohan", "+91 98000 00015",
     ["Vendor management", "Negotiation", "Inventory", "Logistics"]),
    ("meera", "Meera Shah", f"meera@{ORG_DOMAIN}", "Accounts Manager",
     "Finance", "asha", "+91 98000 00016",
     ["Tally", "GST compliance", "Invoicing", "Payroll"]),
    ("vikram", "Vikram Das", f"vikram@{ORG_DOMAIN}", "Site Supervisor",
     "Projects", "rohan", "+91 98000 00017",
     ["Carpentry", "Electrical coordination", "Quality checks", "Site safety"]),
    ("neha", "Neha Kulkarni", f"neha@{ORG_DOMAIN}", "Sales Lead", "Sales",
     "asha", "+91 98000 00018",
     ["Client consultation", "Quotations", "CRM", "Showroom sales"]),
]
EMAIL = {k: e for k, _, e, *_ in TEAM}
NAME = {k: n for k, n, *_ in TEAM}


def refuse_non_local() -> None:
    for what, url in (("database", DSN), ("gateway", GATEWAY)):
        host = urlparse(url.replace("postgresql+psycopg", "postgresql")).hostname
        if host not in ("127.0.0.1", "localhost"):
            sys.exit(f"refusing: the {what} URL is not local ({host})")


# ── Plumbing ──────────────────────────────────────────────────────────────

class Api:
    def __init__(self) -> None:
        self.http = httpx.Client(base_url=GATEWAY, timeout=60)

    def call(self, method: str, path: str, as_: str = ME, **kw):
        headers = {
            "Authorization": f"Bearer {TOKEN}",
            "X-User-Email": as_,
            "X-User-Role": "executive" if as_ == ME else "employee",
        }
        r = self.http.request(method, path, headers=headers, **kw)
        if r.status_code >= 400:
            raise RuntimeError(f"{method} {path} -> {r.status_code}: {r.text[:400]}")
        return r.json() if r.content else None


def connect() -> psycopg.Connection:
    dsn = DSN.replace("postgresql+psycopg://", "postgresql://")
    conn = psycopg.connect(dsn, autocommit=True)
    return conn


def org_id(conn: psycopg.Connection) -> str:
    row = conn.execute(
        "SELECT id::text FROM organization WHERE slug = 'default'"
    ).fetchone()
    if not row:
        sys.exit("no 'default' organization: start the gateway once first")
    return row[0]


def bind(conn: psycopg.Connection, org: str) -> None:
    """Bind the tenant, so FORCE ROW LEVEL SECURITY admits the writes."""
    conn.execute("SELECT set_config('app.tenant_id', %s, false)", (org,))


# ── 1. The organization and its members ───────────────────────────────────

def seed_org_and_people(conn: psycopg.Connection, org: str) -> None:
    conn.execute(
        "UPDATE organization SET display_name = %s, domain = %s WHERE id = %s",
        (ORG_NAME, ORG_DOMAIN, org),
    )
    member_role = conn.execute(
        "SELECT id FROM org_role WHERE organization_id = %s AND slug = 'member'",
        (org,),
    ).fetchone()[0]

    for _key, name, email, title, dept, _mgr, phone, _skills in TEAM:
        conn.execute(
            "INSERT INTO user_identity (email, display_name) VALUES (%s, %s) "
            "ON CONFLICT (lower(email)) DO UPDATE SET display_name = EXCLUDED.display_name",
            (email, name),
        )
        ident = conn.execute(
            "SELECT id FROM user_identity WHERE lower(email) = lower(%s)", (email,)
        ).fetchone()[0]
        existing = conn.execute(
            "SELECT id FROM app_user WHERE lower(email) = lower(%s)", (email,)
        ).fetchone()
        if existing:
            conn.execute(
                "UPDATE app_user SET display_name = %s, organization_id = %s, "
                "status = 'active' WHERE id = %s",
                (name, org, existing[0]),
            )
            user_id = existing[0]
        else:
            user_id = conn.execute(
                "INSERT INTO app_user (email, display_name, role, organization_id, "
                "status, joined_at) VALUES (%s, %s, 'employee', %s, 'active', now()) "
                "RETURNING id",
                (email, name, org),
            ).fetchone()[0]
            conn.execute(
                "INSERT INTO user_role (user_id, role_id, assigned_by, user_identity_id) "
                "VALUES (%s, %s, 'demo-seed', %s) ON CONFLICT DO NOTHING",
                (user_id, member_role, ident),
            )
        conn.execute(
            "INSERT INTO org_membership (organization_id, user_id, status, joined_at) "
            "VALUES (%s, %s, 'active', now()) ON CONFLICT DO NOTHING",
            (org, ident),
        )
        # The trigger on app_user made the directory row. Fill it in.
        conn.execute(
            "UPDATE people SET name = %s, title = %s, role = %s, department = %s, "
            "phone = %s, location = 'Bengaluru', timezone = 'Asia/Kolkata', "
            "status = 'active', employment_type = 'full_time' "
            "WHERE lower(email) = lower(%s)",
            (name, title, title, dept, phone, email),
        )

    people = dict(conn.execute(
        "SELECT lower(email), id FROM people WHERE organization_id = %s", (org,)
    ).fetchall())
    for _key, _n, email, _t, _d, mgr, _p, skills in TEAM:
        pid = people[email.lower()]
        if mgr:
            conn.execute(
                "UPDATE people SET manager_id = %s, reports_to = %s WHERE id = %s",
                (people[EMAIL[mgr].lower()], NAME[mgr], pid),
            )
        conn.execute("UPDATE people SET skills = %s WHERE id = %s", (skills, pid))
        for i, skill in enumerate(skills):
            conn.execute(
                "INSERT INTO people_skills (organization_id, person_id, skill, level, "
                "years, evidence, updated_by) VALUES (%s, %s, %s, %s, %s, 'manual', "
                "'demo-seed') ON CONFLICT DO NOTHING",
                (org, pid, skill, "expert" if i == 0 else ("proficient" if i < 3 else "working"), 3 + (i * 2) % 7),
            )
    print(f"  org renamed to {ORG_NAME!r}, {len(TEAM)} members")


# ── 2. Projects ───────────────────────────────────────────────────────────

CAFE_TASKS = [
    # title, status, assignee keys, importance 0-3, due offset, tags, estimate mins
    ("Site measurement and photo survey", "Done", ["vikram"], 2, -10, ["site"], 180),
    ("Moodboard and material palette sign-off", "Done", ["priya"], 2, -6, ["design"], 240),
    ("Finalise layout with 42 covers", "Done", ["arjun", "priya"], 3, -4, ["design"], 300),
    ("3D renders for counter and seating", "Client review", ["priya"], 3, 1, ["design", "client"], 480),
    ("Share revised quote with café owner", "Client review", ["neha"], 3, 0, ["client", "quote"], 60),
    ("Order oak veneer and terrazzo samples", "In progress", ["karthik"], 2, 2, ["procurement"], 90),
    ("Electrical and lighting plan", "In progress", ["arjun"], 2, 3, ["design"], 360),
    ("Book carpentry crew for 26 Oct start", "In progress", ["vikram"], 1, 4, ["site"], 60),
    ("Kitchen exhaust coordination with MEP vendor", "To do", ["rohan"], 2, 5, ["site"], 120),
    ("Collect 40% advance invoice", "To do", ["meera"], 3, 2, ["finance"], 30),
    ("Custom bar stools: shop drawings", "To do", ["arjun"], 1, 8, ["design", "furniture"], 240),
    ("Signage and menu board concepts", "Backlog", ["priya"], 0, 14, ["design"], 180),
    ("Plant styling and planters shortlist", "Backlog", [], 0, 16, ["styling"], 90),
]

OTHER_PROJECTS = {
    "Whitefield Villa — Living Room": [
        ("Sofa fabric shortlist (3 options)", "In progress", ["priya"], 2, 1, ["client"], 120),
        ("TV wall panelling drawings", "To do", ["arjun"], 2, 4, ["design"], 240),
        ("Confirm rug size with homeowner", "Client review", ["neha"], 1, 0, ["client"], 30),
        ("Curtain motorisation quote", "To do", ["karthik"], 1, 6, ["procurement"], 60),
        ("Site handover checklist", "Backlog", ["vikram"], 0, 20, ["site"], 60),
    ],
    "Koramangala Office Refresh": [
        ("Workstation layout for 28 seats", "Done", ["arjun"], 2, -3, ["design"], 300),
        ("Acoustic panel vendor comparison", "In progress", ["karthik"], 2, 2, ["procurement"], 120),
        ("Meeting room glazing film approval", "Client review", ["rohan"], 1, 1, ["client"], 45),
        ("Weekend install plan with facilities", "To do", ["vikram"], 2, 5, ["site"], 90),
    ],
    "HSR Showroom Launch": [
        ("Showroom floor plan and display zones", "In progress", ["priya"], 2, 3, ["design"], 240),
        ("Launch invite list and walkthrough slots", "To do", ["neha"], 1, 9, ["sales"], 60),
        ("Price tags and product cards", "Backlog", ["meera"], 0, 18, ["sales"], 60),
    ],
}

DESCRIPTIONS = {
    "3D renders for counter and seating": (
        "Two views: the coffee counter from the entrance, and the window "
        "bench seating. Use the terrazzo top and the warm oak option from "
        "the moodboard. The owner wants to see them before Wednesday."
    ),
    "Share revised quote with café owner": (
        "Revised quote after the layout change to 42 covers. Bar stools "
        "moved to custom. Keep the total under ₹18.5 lakh."
    ),
    "Order oak veneer and terrazzo samples": (
        "Two oak veneer finishes and three terrazzo chips. Courier to the "
        "studio, and a second set to site for the owner."
    ),
}

COMMENTS = {
    "3D renders for counter and seating": [
        ("priya", "First pass is up in the project folder. Counter view needs one more lighting pass."),
        ("rohan", "Looks great. Can we try the pendant cluster a little lower?"),
    ],
    "Order oak veneer and terrazzo samples": [
        ("karthik", "Vendor confirmed dispatch on Tuesday. Tracking to follow."),
    ],
}

STATUS_COLOURS = {"Client review": "amber"}


def ymd(day: date) -> str:
    """A due time of 18:00 IST. A bare date reads as midnight, so a task due
    today would show as overdue from the first minute of the day."""
    return f"{day.isoformat()}T18:00:00+05:30"


def seed_projects(api: Api) -> dict[str, str]:
    space = api.call("POST", "/projects/nodes", json={
        "name": "Client Projects", "description": "Live client work for the studio.",
        "lead": EMAIL["rohan"], "task_prefix": "KC",
    })
    sid = space["id"]
    api.call("POST", f"/projects/nodes/{sid}/statuses", json={
        "name": "Client review", "color": "amber", "position": 35,
        "category": "in_progress",
    })
    statuses = {
        s["name"]: s["id"]
        for s in api.call("GET", f"/projects/nodes/{sid}/statuses")["rows"]
    }

    ids: dict[str, str] = {"__space__": sid}
    projects = {"Indiranagar Café Fit-out": CAFE_TASKS, **OTHER_PROJECTS}
    leads = {
        "Indiranagar Café Fit-out": "rohan",
        "Whitefield Villa — Living Room": "priya",
        "Koramangala Office Refresh": "rohan",
        "HSR Showroom Launch": "neha",
    }
    for pname, tasks in projects.items():
        proj = api.call("POST", "/projects/nodes", json={
            "name": pname, "parent_project_id": sid, "lead": EMAIL[leads[pname]],
        })
        ids[pname] = proj["id"]
        for title, status, who, imp, due, tags, est in tasks:
            body = {
                "project_id": proj["id"], "title": title,
                "status_id": statuses[status], "importance": imp,
                "due_at": ymd(d(due)), "tags": tags, "estimate_mins": est,
            }
            if title in DESCRIPTIONS:
                body["description"] = DESCRIPTIONS[title]
            task = api.call("POST", "/projects/tasks", json=body)
            ids[title] = task["id"]
            if who:
                api.call("PUT", f"/projects/tasks/{task['id']}/assignees",
                         json={"assignees": [EMAIL[k] for k in who]})
            for key, text in COMMENTS.get(title, []):
                api.call("POST", f"/projects/tasks/{task['id']}/comments",
                         as_=EMAIL[key], json={"body": text})
    n = sum(len(t) for t in projects.values())
    print(f"  1 space, {len(projects)} projects, {n} tasks")
    return ids


# ── 3. My Tasks and Calendar (the signed-in founder's own lens) ───────────

#: (title, project name or None for a personal task, context, day offset from
#: the demo Monday, start hh:mm, end hh:mm, co-assignee keys, importance)
#: A block on day -3 is Friday 9 October, the day before the demo weekend.
SCHEDULED = [
    ("Review café 3D renders with Priya", "Indiranagar Café Fit-out", "@office", -3, (10, 0), (11, 30), ["priya"], 3),
    ("Call café owner about the revised quote", None, "@calls", -3, (12, 0), (12, 30), [], 2),
    ("Shortlist portfolio photos for the website", None, "@computer", -3, (15, 0), (16, 30), [], 1),
    ("Weekly studio stand-up", None, "@office", 0, (9, 30), (10, 0), [], 1),
    ("Decide sofa fabric with the Whitefield homeowner", "Whitefield Villa — Living Room", "@calls", 0, (11, 0), (12, 0), ["priya"], 2),
    ("Approve October vendor payments", None, "@computer", 0, (15, 0), (16, 0), [], 3),
    ("Site visit: Indiranagar café", "Indiranagar Café Fit-out", "@errands", 1, (10, 0), (12, 0), ["vikram"], 2),
    ("Sign off meeting room glazing film", "Koramangala Office Refresh", "@office", 1, (14, 30), (15, 30), ["rohan"], 1),
    ("Showroom launch plan review", "HSR Showroom Launch", "@office", 2, (9, 30), (11, 0), ["neha"], 2),
    ("Sign the café carpentry contract", "Indiranagar Café Fit-out", "@office", 2, (16, 0), (17, 0), [], 3),
    ("New client consult: Sadashivanagar apartment", None, "@calls", 3, (11, 0), (12, 0), [], 2),
    ("Deep work: HSR showroom concept boards", "HSR Showroom Launch", "@computer", 3, (14, 0), (16, 0), [], 2),
    ("Monthly accounts review with Meera", None, "@office", 4, (10, 0), (11, 0), [], 2),
    ("Plan next week", None, "@computer", 4, (15, 0), (15, 30), [], 1),
]

UNSCHEDULED_NEXT = [
    ("Send thank-you note to the Jayanagar client", "@computer", 1),
    ("Reply to the design school about the guest lecture", "@computer", 0),
    ("Pick up fabric swatches from the Commercial Street store", "@errands", 1),
]

INBOX = [
    "Call Ramesh about Italian marble for the villa",
    "Look through the new handloom rug catalogue",
    "Renew studio insurance before 31 October",
]


def iso(dt: datetime) -> str:
    return dt.isoformat()


def seed_my_tasks(api: Api, projects: dict[str, str]) -> None:
    api.call("POST", "/projects/my/project")
    statuses = {
        s["name"]: s["id"]
        for s in api.call(
            "GET", f"/projects/nodes/{projects['__space__']}/statuses"
        )["rows"]
    }

    def personal(task_id: str, **overlay) -> None:
        api.call("PATCH", f"/projects/tasks/{task_id}/personal", json=overlay)

    for title, pname, ctx, day, start, end, others, imp in SCHEDULED:
        if pname:
            task = api.call("POST", "/projects/tasks", json={
                "project_id": projects[pname], "title": title,
                "status_id": statuses["To do"], "importance": imp,
                "due_at": ymd(d(day)),
                "estimate_mins": (end[0] * 60 + end[1]) - (start[0] * 60 + start[1]),
            })
            api.call("PUT", f"/projects/tasks/{task['id']}/assignees",
                     json={"assignees": [ME] + [EMAIL[k] for k in others]})
        else:
            task = api.call("POST", "/projects/my/tasks", json={
                "title": title, "due_at": ymd(d(day)),
            })
            api.call("PATCH", f"/projects/tasks/{task['id']}", json={"importance": imp})
        personal(
            task["id"], disposition="NEXT", context=ctx, next_action=title,
            scheduled_start=iso(at(day, *start)), scheduled_end=iso(at(day, *end)),
        )

    for title, ctx, imp in UNSCHEDULED_NEXT:
        task = api.call("POST", "/projects/my/tasks", json={"title": title})
        api.call("PATCH", f"/projects/tasks/{task['id']}", json={"importance": imp})
        personal(task["id"], disposition="NEXT", context=ctx, next_action=title)

    for title in INBOX:
        api.call("POST", "/projects/my/tasks", json={"title": title})

    task = api.call("POST", "/projects/my/tasks", json={
        "title": "Final price for the café bar stools", "due_at": ymd(d(1)),
    })
    personal(
        task["id"], disposition="WAITING",
        waiting_on={"name": NAME["karthik"], "email": EMAIL["karthik"]},
        delegated_at=iso(at(-5, 11)), expected_by=iso(at(1, 18)),
    )
    task = api.call("POST", "/projects/my/tasks", json={
        "title": "Explore a second studio in Pune",
    })
    personal(task["id"], disposition="SOMEDAY")
    print(f"  {len(SCHEDULED)} scheduled blocks, "
          f"{len(UNSCHEDULED_NEXT)} next actions, {len(INBOX)} inbox items")


# ── 4. My WhatsApp (data a Meta webhook would normally deliver) ───────────
#
# The account is a Cloud API number with a placeholder credential. Nothing on
# page load decrypts it, and no worker polls Meta, so the row stays inert.
# Times are relative to the moment the seed runs, because the inbox reads
# "service window open" and the relative ages off now().

WA_NUMBER = "+919800000010"
WA_LABELS = [("lbl-hot", "Hot lead", "#25D366"), ("lbl-quote", "Quote sent", "#53BDEB"),
             ("lbl-site", "Site visit", "#FFD279"), ("lbl-pay", "Payment", "#FF9DFF")]
WA_CATEGORIES = [
    # name, icon, notify, auto reply, draft, escalate mins
    ("VIP", "star", "instant", "never", "always", 120),
    ("New customer", "tag", "digest", "holding", "on_intent", 1440),
    ("Pending payment", "tag", "instant", "never", "always", 240),
    ("Vendors", "tag", "digest", "never", "on_intent", 1440),
]

#: name, phone digits, category, status, labels, snooze hours, messages.
#: A message is (direction, minutes ago, text, intent). "commit" marks the
#: inbound message that carries a promise the customer made.
WA_CHATS = [
    ("Sameer Khan · Brewhouse Café", "919800000021", "VIP", "NEEDS_REPLY", ["lbl-quote"], None, [
        ("out", 1500, "Hi Sameer, the revised quote for 42 covers is in your email. The custom bar stools are now included.", None),
        ("in", 1440, "Thanks Asha. Going through it with my partner tonight.", None),
        ("in", 95, "We are fine with the total. Can we start carpentry on 26 Oct as planned?", "scheduling"),
        ("in", 92, "Also, please send the 40% advance invoice. I will transfer it by Monday.", "payment"),
    ]),
    ("Anita Desai", "919800000022", "VIP", "NEEDS_REPLY", ["lbl-site"], None, [
        ("in", 2900, "Hi! Loved the living room renders 😍", "social"),
        ("out", 2880, "So glad you liked them, Anita. Next step is the sofa fabric.", None),
        ("in", 180, "Can you drop the three fabric samples on Saturday morning? Anytime after 10 works.", "scheduling"),
    ]),
    ("Farhan Sheikh", "919800000023", "New customer", "NEEDS_REPLY", ["lbl-hot"], None, [
        ("in", 240, "Hello, I got your number from Instagram. We just bought a 3BHK in HSR Layout.", None),
        ("in", 238, "Looking for a modular kitchen and two wardrobes. What would a rough quote be?", "quote_request"),
    ]),
    ("Kavya Reddy", "919800000024", "Pending payment", "NEEDS_REPLY", ["lbl-pay"], None, [
        ("out", 4400, "Hi Kavya, a gentle reminder for the second instalment of ₹2,40,000.", None),
        ("in", 60, "Done! Transferred the second instalment just now. UTR 4521 8890 1123.", "payment"),
    ]),
    ("Sanjay Gupta", "919800000025", None, "NEEDS_REPLY", [], None, [
        ("in", 300, "Hi, one of the wardrobe shutters in the master bedroom is not closing properly.", "service_issue"),
        ("in", 299, "Can someone come and check the hinge this week?", "service_issue"),
    ]),
    ("Lakshmi Venkatesh", "919800000026", "VIP", "AWAITING", ["lbl-quote"], None, [
        ("in", 1600, "When will the dining table be delivered?", "order_status"),
        ("out", 1560, "The table is in finishing. We will deliver on Thursday between 11 and 1. Does that work?", None),
        ("in", 1500, "Let me check with my husband and confirm by tomorrow.", "commit"),
        ("out", 400, "Hi Lakshmi, just checking if Thursday works for the delivery.", None),
    ]),
    ("Deepak Joshi · Koramangala Office", "919800000027", None, "AWAITING", ["lbl-site"], None, [
        ("in", 3000, "Facilities team is ok with a weekend install for the acoustic panels.", "scheduling"),
        ("out", 2950, "Great. We plan Saturday 8 AM to 6 PM. Please share the security pass list by Friday.", None),
        ("in", 2900, "Will send the pass list by Friday.", "commit"),
    ]),
    ("Pooja Menon", "919800000028", None, "DONE", [], None, [
        ("in", 5000, "The bookshelf looks amazing in the study. Thank you to the whole team!", "social"),
        ("out", 4980, "Thank you, Pooja! Please share a photo when the books are in 📚", None),
    ]),
    ("Rahul Bhat", "919800000029", "New customer", "NEEDS_REPLY", ["lbl-hot"], 72, [
        ("in", 1800, "Interested in a full home interior for our new flat in Hebbal.", "quote_request"),
        ("out", 1780, "Thanks, Rahul. Can we set up a call this week to understand the brief?", None),
        ("in", 1700, "We will decide after Diwali. Please ping me then.", None),
    ]),
]


def seed_whatsapp(conn: psycopg.Connection) -> None:
    now = anchor_now()
    # Re-runnable: the account cascades to every chat, message and label.
    conn.execute(
        "DELETE FROM wa_accounts WHERE user_id = %s AND phone_number = %s",
        (ME, WA_NUMBER),
    )
    acct = conn.execute(
        "INSERT INTO wa_accounts (user_id, phone_number, phone_number_id, waba_id, "
        "display_name, credentials_encrypted, quality_rating, sync_status, "
        "initial_sync_done, last_synced_at, is_default, provider, created_at) "
        "VALUES (%s, %s, '100000000000001', '200000000000001', %s, "
        "'demo-placeholder-not-a-credential', 'GREEN', 'live', true, now(), true, "
        "'cloud_api', now() - interval '60 days') RETURNING id",
        (ME, WA_NUMBER, ORG_NAME),
    ).fetchone()[0]
    for i, (name, icon, notify, auto, draft, esc) in enumerate(WA_CATEGORIES):
        conn.execute(
            "INSERT INTO wa_categories (account_id, name, icon, notify_policy, "
            "auto_reply_policy, draft_policy, escalate_after_mins, sort_order) "
            "VALUES (%s, %s, %s, %s, %s, %s, %s, %s)",
            (acct, name, icon, notify, auto, draft, esc, (i + 1) * 10),
        )
    for i, (lid, name, color) in enumerate(WA_LABELS):
        conn.execute(
            "INSERT INTO wa_labels (account_id, wa_label_id, name, color, sync_state, "
            "list_type, sort_order) VALUES (%s, %s, %s, %s, 'synced', 'CUSTOM', %s)",
            (acct, lid, name, color, (i + 1) * 10),
        )

    n_msgs = 0
    for c_i, (name, digits, cat, status, labels, snooze_h, msgs) in enumerate(WA_CHATS):
        last_ago = min(m[1] for m in msgs)
        last_in_ago = min((m[1] for m in msgs if m[0] == "in"), default=None)
        window = (now + timedelta(hours=24) - timedelta(minutes=last_in_ago)
                  if last_in_ago is not None else now - timedelta(hours=1))
        chat = conn.execute(
            "INSERT INTO wa_chats (account_id, wa_chat_id, kind, name, participants, "
            "category, service_window_expires_at, last_message_at) "
            "VALUES (%s, %s, 'dm', %s, %s, %s, %s, %s) RETURNING id",
            (acct, digits, name,
             json.dumps([{"wa_id": digits, "name": name}]), cat, window,
             now - timedelta(minutes=last_ago)),
        ).fetchone()[0]
        conn.execute(
            "INSERT INTO wa_contacts (account_id, phone_number, wa_id, display_name, "
            "category, category_source, in_address_book) "
            "VALUES (%s, %s, %s, %s, %s, 'user', true)",
            (acct, digits, digits, name, cat),
        )
        last_id = None
        for m_i, (direction, ago, text, intent) in enumerate(msgs):
            commit = intent == "commit"
            mid = conn.execute(
                "INSERT INTO wa_messages (account_id, chat_id, wa_message_id, "
                "direction, sender, kind, body_text, intent, send_regime, sent_at, "
                "synced_at, delivery_status, rules_processed_at, "
                "commitment_checked_at) VALUES (%s, %s, %s, %s, %s, 'text', %s, %s, "
                "%s, %s, %s, %s, now(), now()) RETURNING id",
                (acct, chat, f"wamid.demo.{c_i:02d}.{m_i:02d}", direction,
                 json.dumps({"wa_id": digits, "name": name} if direction == "in" else {}),
                 text, None if commit else intent,
                 "session" if direction == "out" else None,
                 now - timedelta(minutes=ago), now - timedelta(minutes=ago),
                 "read" if direction == "out" else None),
            ).fetchone()[0]
            n_msgs += 1
            if commit:
                conn.execute(
                    "INSERT INTO wa_commitments (account_id, chat_id, message_id, "
                    "direction, text, due_hint, status) "
                    "VALUES (%s, %s, %s, 'theirs', %s, %s, 'open')",
                    (acct, chat, mid, text,
                     "by friday" if "Friday" in text else "by tomorrow"),
                )
            last_id = mid
        conn.execute(
            "INSERT INTO wa_chat_status (account_id, chat_id, status, last_message_id, "
            "last_message_at, reason, classified_at, snoozed_until) "
            "VALUES (%s, %s, %s, %s, %s, %s, now(), %s)",
            (acct, chat, status, last_id, now - timedelta(minutes=last_ago),
             {"NEEDS_REPLY": "they asked a question and await your reply",
              "AWAITING": "you replied and are waiting on them",
              "DONE": "conversation closed", "FYI": "no reply needed"}[status],
             now + timedelta(hours=snooze_h) if snooze_h else None),
        )
        for lid in labels:
            conn.execute(
                "INSERT INTO wa_chat_labels (account_id, wa_chat_id, wa_label_id) "
                "VALUES (%s, %s, %s)", (acct, digits, lid),
            )
    print(f"  1 number, {len(WA_CHATS)} chats, {n_msgs} messages")


# ── 4b. My Email (data a mailbox sync and the AI rules would write) ───────
#
# One mailbox with a placeholder credential and sync turned OFF, so the sync
# worker never picks it up. Do not press "Sync now" in the app: the sync call
# fails on the placeholder and marks the mailbox as needing a reconnect.

MAILBOX = f"asha@{ORG_DOMAIN}"

#: thread key, subject, categories, importance, starred, then the messages.
#: A message is (from name, from email, minutes ago, body). A message from
#: MAILBOX goes to the "sent" folder; every other one goes to the inbox.
EMAIL_THREADS = [
    ("cafe-quote", "Revised quote: Indiranagar café, 42 covers", ["Needs Reply"], "high", True, [
        ("Asha Rao", MAILBOX, 1560,
         "Hi Sameer,\n\nPlease find the revised quote for the café below. We moved the bar "
         "stools to a custom design and adjusted the layout to 42 covers.\n\n"
         "Total: Rs 18,50,000 + GST\nPayment: 40% advance, 40% on carpentry, 20% on handover\n\n"
         "Happy to walk you through it on a call.\n\nWarm regards,\nAsha Rao\nKite & Co. Interiors"),
        ("Sameer Khan", "sameer@brewhouse.example", 110,
         "Hi Asha,\n\nThanks for the revised quote. My partner and I are happy with the total.\n\n"
         "Can we lock 26 October for the carpentry start? Please also send the 40% advance "
         "invoice so we can process it on Monday.\n\nOne small request: can the window bench "
         "seating have storage underneath?\n\nBest,\nSameer\nBrewhouse Café"),
    ]),
    ("villa-fabric", "Sofa fabric options for the living room", ["Needs Reply"], "normal", False, [
        ("Anita Desai", "anita.desai@example.com", 190,
         "Hi Asha,\n\nWe loved the renders! Before we decide on the sofa, could we see the "
         "fabric options in person? The bouclé and the linen blend are our favourites.\n\n"
         "Saturday morning works best for us.\n\nThanks,\nAnita"),
    ]),
    ("woodcraft-invoice", "Invoice INV-2026-0417: oak veneer and terrazzo samples", ["FYI"], "normal", False, [
        ("Woodcraft Supplies Accounts", "accounts@woodcraft-supplies.example", 300,
         "Dear Kite & Co. team,\n\nPlease find our invoice details below.\n\n"
         "Invoice: INV-2026-0417\nItems: Oak veneer samples (2 finishes), terrazzo chips (3)\n"
         "Amount: Rs 14,750 incl. GST\nDue date: 20 October 2026\n\n"
         "Bank details are on file. Thank you for your business.\n\nWoodcraft Supplies"),
    ]),
    ("sarjapur-lead", "Enquiry: full interiors for a 3BHK in Sarjapur", ["Needs Reply"], "high", False, [
        ("Nikhil Rao", "nikhil.shruti@example.com", 420,
         "Hello,\n\nWe found your studio through a friend's home in Jayanagar. We just took "
         "possession of a 3BHK in Sarjapur Road and are looking for full interiors: living, "
         "dining, kitchen and three bedrooms.\n\nOur budget is around Rs 25 lakh. Could we "
         "set up a consultation next week?\n\nRegards,\nNikhil and Shruti"),
    ]),
    ("site-ready", "Café site readiness for 26 Oct", ["FYI"], "normal", False, [
        ("Rohan Mehta", f"rohan@{ORG_DOMAIN}", 600,
         "Hi Asha,\n\nQuick update on the café. Vikram checked the site today.\n\n"
         "- Civil work by the landlord is done\n- Electrical points are marked\n"
         "- Kitchen exhaust needs one more visit from the MEP vendor\n\n"
         "We are on track for the 26 Oct carpentry start. I will confirm the crew by Wednesday.\n\nRohan"),
    ]),
    ("pass-list", "Security pass list for Saturday install", ["Awaiting Reply"], "normal", False, [
        ("Asha Rao", MAILBOX, 1300,
         "Hi Deepak,\n\nFor the acoustic panel install this Saturday (8 AM to 6 PM), could you "
         "share the security pass list by Friday? We will have six people on site.\n\n"
         "Thanks,\nAsha"),
    ]),
    ("lumen-quote", "Quote: pendant lights for the café counter", ["Needs Reply"], "normal", False, [
        ("Lumen Studio", "sales@lumenstudio.example", 900,
         "Hi Asha,\n\nAs discussed, here is our quote for the counter pendants.\n\n"
         "6 x brass dome pendant, 2700K: Rs 1,08,000\nInstallation: Rs 9,000\n"
         "Lead time: 3 weeks from order\n\nPlease confirm by Tuesday to hold this batch.\n\n"
         "Best,\nLumen Studio"),
    ]),
    ("gst-sept", "GST filing for September: two invoices to confirm", ["Needs Reply"], "normal", False, [
        ("Meera Shah", f"meera@{ORG_DOMAIN}", 1500,
         "Hi Asha,\n\nI am closing the September GST return. Two vendor invoices are missing "
         "from the books: Woodcraft (Rs 14,750) and the Koramangala glass vendor (Rs 62,000).\n\n"
         "Can you confirm both are approved? The filing is due on the 20th.\n\nMeera"),
    ]),
    ("bank-credit", "Credit alert: Rs 2,40,000 received", ["Receipt"], "normal", False, [
        ("Bank Alerts", "alerts@bank.example", 70,
         "Your account XX4821 has been credited with Rs 2,40,000.00 today.\n"
         "Reference: UTR 452188901123, from KAVYA REDDY.\n\nThis is an automated message."),
    ]),
    ("launch-invite", "Invitation: HSR showroom walkthrough, Wed 14 Oct", ["Calendar"], "normal", False, [
        ("Neha Kulkarni", f"neha@{ORG_DOMAIN}", 2000,
         "You are invited to the HSR showroom launch walkthrough.\n\n"
         "When: Wednesday 14 October, 9:30 to 11:00\nWhere: HSR Layout showroom\n\n"
         "We will walk through the display zones and the launch invite list."),
    ]),
    ("courier", "Your shipment is out for delivery", ["Notification"], "low", False, [
        ("BlueLine Couriers", "noreply@blueline.example", 240,
         "Your shipment 88214077 (fabric swatches) is out for delivery and will arrive today "
         "by 6 PM."),
    ]),
    ("ceiling", "Re: Whitefield villa false ceiling drawings", ["Done"], "normal", False, [
        ("Vivek Menon", "vivek@studio-arc.example", 3100,
         "Hi Asha,\n\nAttached the revised false ceiling drawings with the cove lighting "
         "detail. Let me know if the client wants the deeper cove."),
        ("Asha Rao", MAILBOX, 2950,
         "Thanks Vivek, these look right. The client approved the standard cove. "
         "We will go ahead."),
    ]),
    ("design-week", "Design Week Bengaluru: early-bird passes close Sunday", ["Newsletter"], "low", False, [
        ("Design Week Bengaluru", "hello@designweek.example", 1200,
         "Early-bird passes for Design Week Bengaluru close this Sunday. Talks, studio tours "
         "and a materials expo, 5 to 8 November."),
    ]),
    ("cold", "Grow your studio's Instagram reach 10x", ["Cold Email"], "low", False, [
        ("Ravi from GrowthHub", "ravi@growthhub.example", 800,
         "Hi there, we help interior studios get 10x more reach on Instagram. "
         "Do you have 15 minutes this week?"),
    ]),
]

#: thread key -> (status, reason) for the triage store the AI rules keep.
THREAD_STATUS = {
    "cafe-quote": ("NEEDS_REPLY", "Client accepted the quote and asks for the advance invoice"),
    "villa-fabric": ("NEEDS_REPLY", "Client asks to see fabric samples on Saturday"),
    "sarjapur-lead": ("NEEDS_REPLY", "New lead asks for a consultation next week"),
    "lumen-quote": ("NEEDS_REPLY", "Vendor needs a confirmation by Tuesday"),
    "gst-sept": ("NEEDS_REPLY", "Accounts needs two invoices approved before filing"),
    "pass-list": ("AWAITING", "You asked for the pass list; no reply yet"),
    "site-ready": ("FYI", "Status update, no action needed"),
    "woodcraft-invoice": ("FYI", "Vendor invoice for the books"),
    "ceiling": ("DONE", "Drawings approved"),
}

#: thread key -> (domain, fact type, direction, title, counterpart, ref,
#: amount, due offset in days from today)
INSIGHTS = {
    "woodcraft-invoice": ("finance", "invoice", "payable", "Invoice INV-2026-0417 from Woodcraft Supplies",
                          "Woodcraft Supplies", "INV-2026-0417", 14750, 11),
    "cafe-quote": ("sales", "deal_signal", None, "Brewhouse Café accepted the Rs 18.5 lakh quote",
                   "Brewhouse Café", None, 1850000, None),
    "sarjapur-lead": ("sales", "lead", None, "3BHK full interiors in Sarjapur, budget Rs 25 lakh",
                      "Nikhil Rao", None, 2500000, None),
    "lumen-quote": ("sales", "quote", "payable", "Lumen Studio pendant lights quote",
                    "Lumen Studio", None, 117000, 4),
    "gst-sept": ("finance", "payment_request", None, "September GST return due",
                 "Meera Shah", None, None, 11),
}

DRAFT_REPLY = (
    "villa-fabric",
    "Hi Anita,\n\nSo glad you liked the renders! Saturday works well. Priya will bring "
    "the bouclé, the linen blend and one wool option at 10:30 AM.\n\n"
    "Warm regards,\nAsha",
)


def html(body: str) -> str:
    paras = [p.replace("\n", "<br>") for p in body.split("\n\n")]
    return "".join(f"<p>{p}</p>" for p in paras)


def seed_email(conn: psycopg.Connection, org: str) -> None:
    now = anchor_now()
    # Re-runnable: the mailbox cascades to every message, rule log and insight.
    conn.execute(
        "DELETE FROM email_accounts WHERE user_id = %s AND email_address = %s",
        (ME, MAILBOX),
    )
    acct = conn.execute(
        "INSERT INTO email_accounts (organization_id, user_id, provider, email_address, "
        "label, credentials_encrypted, sync_enabled, sync_status, initial_sync_done, "
        "is_default, onboarding_done_at, in_all_inboxes, color_slot, last_synced_at) "
        "VALUES (%s, %s, 'google', %s, 'Studio', 'demo-placeholder-not-a-credential', "
        "false, 'idle', true, true, now(), true, 3, now()) RETURNING id",
        (org, ME, MAILBOX),
    ).fetchone()[0]
    me_addr = {"name": "Asha Rao", "email": MAILBOX}
    n = 0
    for t_i, (key, subject, cats, imp, star, msgs) in enumerate(EMAIL_THREADS):
        thread = f"demo-thread-{key}"
        first_in = None
        last_id = last_at = None
        for m_i, (name, addr, ago, body) in enumerate(msgs):
            mine = addr == MAILBOX
            sender = {"name": name, "email": addr}
            other = next(({"name": nm, "email": a} for nm, a, *_ in msgs if a != MAILBOX),
                         {"name": "Deepak Joshi", "email": "deepak.joshi@example.com"})
            received = now - timedelta(minutes=ago)
            mid = conn.execute(
                "INSERT INTO email_messages (account_id, provider_message_id, thread_id, "
                "folder, categories, importance, from_address, to_addresses, subject, "
                "snippet, body_text, body_html, is_read, is_starred, received_at, "
                "rules_processed_at, internet_message_id) VALUES (%s, %s, %s, %s, %s, %s, "
                "%s, %s, %s, %s, %s, %s, %s, %s, %s, now(), %s) RETURNING id",
                (acct, f"demo-{t_i:02d}-{m_i:02d}", thread,
                 "sent" if mine else "inbox", cats, imp, json.dumps(sender),
                 json.dumps([other if mine else me_addr]),
                 subject if m_i == 0 else f"Re: {subject}",
                 " ".join(body.split())[:180], body, html(body),
                 mine or ago > 1000 or "Done" in cats, star and not mine, received,
                 f"<demo-{t_i:02d}-{m_i:02d}@{ORG_DOMAIN}>"),
            ).fetchone()[0]
            n += 1
            last_id, last_at = mid, received
            if not mine and first_in is None:
                first_in = (mid, sender)
        if first_in:
            mid, sender = first_in
            conn.execute(
                "INSERT INTO email_senders (account_id, email, name, category, "
                "categorized_at, category_source) VALUES (%s, %s, %s, %s, now(), 'ai') "
                "ON CONFLICT DO NOTHING",
                (acct, sender["email"], sender["name"],
                 "Newsletter" if "Newsletter" in cats else
                 "Cold Email" if "Cold Email" in cats else
                 "Notification" if set(cats) & {"Notification", "Receipt"} else
                 "Conversation"),
            )
            conn.execute(
                "INSERT INTO email_executed_rules (account_id, rule_name, message_id, "
                "provider_message_id, thread_id, subject, from_address, status, "
                "automated, actions_taken, reason, match_source) VALUES (%s, %s, %s, "
                "%s, %s, %s, %s, 'APPLIED', true, %s, %s, 'AI')",
                (acct, cats[0], mid, None, thread, subject, sender["email"],
                 json.dumps(["LABEL"]),
                 THREAD_STATUS.get(key, (None, f"Classified as {cats[0]}"))[1]),
            )
        if key in THREAD_STATUS:
            status, reason = THREAD_STATUS[key]
            conn.execute(
                "INSERT INTO email_thread_status (account_id, thread_id, status, "
                "last_message_id, last_message_at, reason, classified_at) "
                "VALUES (%s, %s, %s, %s, %s, %s, now())",
                (acct, thread, status, last_id, last_at, reason),
            )
        if key in INSIGHTS and first_in:
            dom, fact, direction, title, cpart, ref, amount, due = INSIGHTS[key]
            conn.execute(
                "INSERT INTO email_insights (organization_id, account_id, message_id, "
                "domain, fact_type, direction, title, counterpart, counterpart_email, ref, "
                "amount, currency, due_on, quote, confidence, extractor_version, "
                "dedupe_key, state) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, "
                "'INR', %s, %s, 0.92, 'demo-1', %s, 'open')",
                (org, acct, first_in[0], dom, fact, direction, title, cpart,
                 first_in[1]["email"], ref, amount,
                 (now + timedelta(days=due)).date() if due is not None else None,
                 subject, f"demo:{key}"),
            )

    key, body = DRAFT_REPLY
    conn.execute(
        "INSERT INTO email_messages (account_id, provider_message_id, thread_id, folder, "
        "categories, from_address, to_addresses, subject, snippet, body_text, body_html, "
        "is_read, received_at) VALUES (%s, 'demo-draft-01', %s, 'drafts', '{}', %s, %s, "
        "%s, %s, %s, %s, true, now())",
        (acct, f"demo-thread-{key}", json.dumps(me_addr),
         json.dumps([{"name": "Anita Desai", "email": "anita.desai@example.com"}]),
         "Re: Sofa fabric options for the living room", " ".join(body.split())[:180],
         body, html(body)),
    )
    conn.execute(
        "INSERT INTO email_ai_drafts (account_id, thread_id, draft_text) "
        "VALUES (%s, %s, %s) ON CONFLICT DO NOTHING",
        (acct, f"demo-thread-{key}", body),
    )
    print(f"  1 mailbox, {len(EMAIL_THREADS)} threads, {n} messages, 1 AI draft")


# ── 5. Chat history (stored turns; no model is called) ────────────────────

CHATS = [
    ("What needs my attention this week?", 26 * 60, [
        ("user", "What needs my attention this week?"),
        ("assistant",
         "Here is what stands out for the week of **12 October**:\n\n"
         "1. **Indiranagar Café Fit-out** — Sameer accepted the revised quote. "
         "The 40% advance invoice is still to go out, and carpentry starts on 26 Oct.\n"
         "2. **Whitefield Villa** — Anita wants the three fabric samples on Saturday "
         "after 10 AM. The sofa fabric decision is on your calendar for Monday at 11.\n"
         "3. **Koramangala Office** — the glazing film needs your sign-off on Tuesday. "
         "Deepak still owes the security pass list for the weekend install.\n"
         "4. **Payments** — Kavya Reddy says she paid the second instalment "
         "(₹2,40,000). Meera should match the UTR before you reply.\n\n"
         "Five WhatsApp chats need a reply. The oldest is Sanjay Gupta's hinge issue."),
        ("user", "Draft a reply to Sameer confirming the 26 Oct start."),
        ("assistant",
         "Here is a draft:\n\n"
         "> Hi Sameer, thank you for confirming the quote. We are all set to start "
         "carpentry on **Monday, 26 October**. Meera will send the 40% advance "
         "invoice today. Vikram will be your site supervisor, and he will share "
         "the daily plan the evening before.\n>\n> Warm regards,\n> Asha\n\n"
         "Shall I queue it for approval, or would you like to change anything?"),
    ]),
    ("Café fit-out budget check", 3 * 24 * 60, [
        ("user", "How is the Indiranagar café budget looking against the quote?"),
        ("assistant",
         "The project is tracking **within budget**. Committed spend is 61% of "
         "the quote, with 45% of the work done or in progress.\n\n"
         "| Head | Quoted | Committed |\n|---|---:|---:|\n"
         "| Carpentry and joinery | ₹7,20,000 | ₹4,10,000 |\n"
         "| Counter and terrazzo | ₹3,80,000 | ₹3,80,000 |\n"
         "| Lighting and electrical | ₹2,60,000 | ₹1,15,000 |\n"
         "| Seating and bar stools | ₹3,40,000 | ₹1,80,000 |\n"
         "| Styling and signage | ₹1,50,000 | — |\n\n"
         "The bar stools moved to custom, so watch that line. Karthik is still "
         "waiting on the final price."),
    ]),
    ("New WhatsApp enquiries", 5 * 24 * 60, [
        ("user", "Summarise the new enquiries that came in on WhatsApp."),
        ("assistant",
         "Two new enquiries:\n\n"
         "- **Farhan Sheikh** — 3BHK in HSR Layout. Wants a modular kitchen and "
         "two wardrobes, and asked for a rough quote. Found you on Instagram.\n"
         "- **Rahul Bhat** — full home interior for a new flat in Hebbal. Wants "
         "to decide after Diwali, so I snoozed the chat until then.\n\n"
         "Neha is the best owner for both. Shall I create follow-up tasks for her?"),
        ("user", "Yes, please."),
        ("assistant",
         "Done. I created two tasks in **HSR Showroom Launch** for Neha:\n\n"
         "- *Call Farhan Sheikh with a rough kitchen and wardrobe estimate* — due Monday\n"
         "- *Follow up with Rahul Bhat after Diwali* — due 3 November"),
    ]),
]


def plain(markdown: str) -> str:
    """The sidebar preview is plain text, so drop the markdown marks."""
    text = markdown.replace("**", "").replace("*", "").replace("> ", "")
    return " ".join(text.split())


def seed_chat(conn: psycopg.Connection) -> None:
    now_ms = int(anchor_now().timestamp() * 1000)
    conn.execute(
        "DELETE FROM chat_session WHERE user_id = %s AND title = ANY(%s)",
        (ME, [c[0] for c in CHATS]),
    )
    for title, ago_min, turns in CHATS:
        # Stable ids: the browser caches sessions, so a re-seed must reuse them.
        sid = str(uuid.uuid5(uuid.NAMESPACE_URL, f"demo-chat:{title}"))
        start = now_ms - ago_min * 60_000
        last = start + (len(turns) - 1) * 45_000
        conn.execute(
            "INSERT INTO chat_session (id, user_id, agent_name, title, last_preview, "
            "message_count, visibility, created_at, updated_at) VALUES "
            "(%s, %s, 'orchestrator', %s, %s, %s, 'private', "
            "to_timestamp(%s / 1000.0), to_timestamp(%s / 1000.0))",
            (sid, ME, title, plain(turns[-1][1])[:120], len(turns), start, last),
        )
        for i, (role, content) in enumerate(turns):
            human = role == "user"
            conn.execute(
                "INSERT INTO chat_message (id, session_id, role, content, timestamp_ms, "
                "author_kind, author_email, run_member_email, run_final_at) "
                "VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)",
                (str(uuid.uuid5(uuid.NAMESPACE_URL, f"{sid}:{i}")), sid, role, content, start + i * 45_000,
                 "human" if human else "agent", ME if human else "orchestrator",
                 None if human else ME,
                 None if human else datetime.now(UTC)),
            )
    print(f"  {len(CHATS)} conversations")


# ── 6. Approvals (actions an agent queued for a human to review) ──────────

APPROVALS = [
    ("agent:email-assistant", "email.send", "sameer@brewhouse.example", 18, {
        "to": "sameer@brewhouse.example",
        "subject": "Indiranagar café: carpentry starts 26 October",
        "body": "Hi Sameer, thank you for confirming the quote. We are all set to "
                "start carpentry on Monday, 26 October. Meera will send the 40% "
                "advance invoice today.",
    }),
    ("agent:finance", "invoice.create", "client:brewhouse-cafe", 9, {
        "customer": "Brewhouse Café (Sameer Khan)",
        "project": "Indiranagar Café Fit-out",
        "lines": [{"item": "Advance, 40% of ₹18,50,000", "amount": 740000}],
        "currency": "INR", "gst_percent": 18, "due_in_days": 7,
    }),
    ("agent:whatsapp-assistant", "whatsapp.send", "whatsapp:+91 98000 00024", 4, {
        "to": "+91 98000 00024",
        "text": "Thank you, Kavya. We have received the second instalment of "
                "₹2,40,000. The receipt will reach your email today.",
    }),
]


def seed_approvals(conn: psycopg.Connection) -> None:
    conn.execute(
        "DELETE FROM pending_actions WHERE status = 'pending' AND target = ANY(%s)",
        ([a[2] for a in APPROVALS],),
    )
    for actor, action, target, ago, args in APPROVALS:
        conn.execute(
            "INSERT INTO pending_actions (actor, action, target, payload, authority, "
            "destructive, disposition, status, created_at) VALUES "
            "(%s, %s, %s, %s, 'suggest+apply', true, 'needs_approval', 'pending', "
            "%s::timestamptz - make_interval(mins => %s))",
            (actor, action, target, json.dumps({"args": args}), anchor_now(), ago),
        )
    print(f"  {len(APPROVALS)} pending approvals")


# ── Entry point ───────────────────────────────────────────────────────────

SECTIONS = ["people", "projects", "mytasks", "whatsapp", "email", "chat",
            "approvals"]


def main() -> None:
    refuse_non_local()
    p = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    p.add_argument("--only", choices=SECTIONS, action="append")
    args = p.parse_args()
    todo = args.only or SECTIONS

    conn = connect()
    org = org_id(conn)
    bind(conn, org)
    api = Api()
    state: dict = {}

    if "people" in todo:
        print("people:")
        seed_org_and_people(conn, org)
    if "projects" in todo:
        print("projects:")
        state["projects"] = seed_projects(api)
    if "mytasks" in todo:
        print("my tasks and calendar:")
        seed_my_tasks(api, state.get("projects") or load_projects(conn))
    if "whatsapp" in todo:
        print("whatsapp:")
        seed_whatsapp(conn)
    if "email" in todo:
        print("email:")
        seed_email(conn, org)
    if "chat" in todo:
        print("chat:")
        seed_chat(conn)
    if "approvals" in todo:
        print("approvals:")
        seed_approvals(conn)
    print("done.")


def load_projects(conn: psycopg.Connection) -> dict[str, str]:
    rows = conn.execute(
        "SELECT name, id::text, parent_project_id FROM pm_projects "
        "WHERE personal_owner IS NULL AND archived_at IS NULL"
    ).fetchall()
    out = {name: pid for name, pid, _ in rows}
    out["__space__"] = next(pid for name, pid, parent in rows
                            if parent is None and name == "Client Projects")
    return out


if __name__ == "__main__":
    main()
