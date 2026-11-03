# pyright: reportCallIssue=false
import os
import secrets
import asyncio
from datetime import datetime
from pathlib import Path
from functools import wraps
from inspect import iscoroutinefunction
from dotenv import load_dotenv

load_dotenv()

# --- IMPORT CHATBOT ---
from chatbot import get_chat_response

from flask import Flask, render_template, request, redirect, url_for, flash, session, jsonify
from flask_cors import CORS
import smtplib
from email.mime.text import MIMEText
from apscheduler.schedulers.background import BackgroundScheduler
import atexit

from extensions import db
from models import User, Ticket, Category, AdminCategories, Feedback, CommonProblem
from ai_engine import analyze_ticket_with_llm 

# ------------------ APP INIT ------------------
app = Flask(__name__, static_folder="static")
CORS(app)
app.secret_key = os.getenv("FLASK_SECRET_KEY", secrets.token_hex(24))

BASE_DIR = Path(__file__).resolve().parent
instance_dir = BASE_DIR / "instance"
instance_dir.mkdir(exist_ok=True)
db_path = instance_dir / "sih_helpdesk.db"

app.config['SQLALCHEMY_DATABASE_URI'] = f"sqlite:///{db_path.as_posix()}"
app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False
db.init_app(app)

AUTO_VERIFY_REGISTRATION = True
SUPER_ADMIN_EMAIL = os.getenv("SUPER_ADMIN_EMAIL", "admin@example.com")
EMAIL_ADDRESS = os.getenv("EMAIL_ADDRESS")
EMAIL_PASSWORD = os.getenv("EMAIL_PASSWORD")

# ------------------ HELPER FUNCTIONS ------------------
def is_valid_email_domain(email):
    ALLOWED_DOMAINS = ["gmail.com", "hotmail.com", "zoho.com", "outlook.com", "yahoo.com", "live.com"]
    try:
        if '@' not in email: return False
        domain = email.split('@')[1].lower().strip()
        return domain in ALLOWED_DOMAINS
    except IndexError: return False

def send_raw_email(to_email, subject, body):
    if not EMAIL_ADDRESS or not EMAIL_PASSWORD:
        print("Email skipped: EMAIL_ADDRESS or EMAIL_PASSWORD is not configured.")
        return

    msg = MIMEText(body)
    msg["Subject"] = subject
    msg["From"] = EMAIL_ADDRESS
    msg["To"] = to_email

    try:
        with smtplib.SMTP_SSL("smtp.gmail.com", 465) as smtp:
            smtp.login(EMAIL_ADDRESS, EMAIL_PASSWORD)
            smtp.sendmail(EMAIL_ADDRESS, to_email, msg.as_string())

        print(f"Email sent to {to_email}")

    except Exception as e:
        print(f"Email Failed to {to_email}: {str(e)}")

def notify_status_change(ticket, recipient_email, type="update"):
    user_name = ticket.creator.name if ticket.creator else "User"
    if type == "new_agent":
        subject = f"[Action Required] New Ticket #{ticket.id}: {ticket.subject}"
        body = f"New Ticket Assigned!\n\nID: #{ticket.id}\nPriority: {ticket.priority}\nDescription:\n{ticket.description}\n\nPlease review on dashboard."
    elif type == "new_user":
        subject = f"[Ticket #{ticket.id}] Received: {ticket.subject}"
        body = f"Hello {user_name},\n\nWe have received your ticket.\nID: #{ticket.id}\nStatus: {ticket.status}\n\nAn agent will review shortly."
    elif type == "update":
        subject = f"[Update] Ticket #{ticket.id} is now {ticket.status.upper()}"
        body = f"Hello {user_name},\n\nYour ticket status is now: {ticket.status.upper()}.\nSubject: {ticket.subject}"
    else: return
    send_raw_email(recipient_email, subject, body)

# ---------------------------------------------------------
#  ROUTING ALGORITHM: AGENT FOCUS + LEAST LOADED
# ---------------------------------------------------------
def get_best_agent_least_loaded(category_obj):
    """
    Finds the best AGENT (Worker) for the job.
    1. Filter users with role='agent'.
    2. Sort by Open Tickets (Low to High).
    3. Tie-breaker: Experience (Low to High).
    """
    # NOTE: We specifically filter for 'agent' only.
    # Managers are EXCLUDED from auto-routing.
    admins = db.session.query(User).join(AdminCategories).filter(
        User.role.in_(['agent']), 
        AdminCategories.category_id == category_obj.id
    ).all()

    if not admins:
        return None

    scored_admins = []
    
    # FIX: Removed emoji causing crash
    print(f"--- Balancing Load for {category_obj.name} ---")

    for admin in admins:
        # A. Count Active (Workload)
        active_count = Ticket.query.filter(
            Ticket.assigned_admin_id == admin.id,
            Ticket.status.in_(['open', 'in-progress'])
        ).count()

        # B. Count Resolved (Experience)
        resolved_count = Ticket.query.filter(
            Ticket.assigned_admin_id == admin.id,
            Ticket.status == 'resolved'
        ).count()
        
        # Store for sorting
        scored_admins.append({
            'admin': admin,
            'open': active_count,
            'resolved': resolved_count
        })
        print(f"   > {admin.name} ({admin.role}): Open={active_count}, Resolved={resolved_count}")

    # SORTING: Primary = Open Tickets (Asc), Secondary = Resolved (Asc)
    scored_admins.sort(key=lambda x: (x['open'], x['resolved']))

    best_match = scored_admins[0]
    best_admin = best_match['admin']

    # FIX: Removed emoji causing crash
    print(f"   WINNER: {best_admin.name} (Has {best_match['open']} open tickets)\n")
    return best_admin

def generate_daily_report():
    with app.app_context():
        print("Generating Daily Report...")
        tickets = Ticket.query.all()
        categories = ['software', 'hardware', 'network', 'database']
        total = len(tickets)
        urgent = len([t for t in tickets if t.priority == 'high'])
        lines = [f"DAILY REPORT - {datetime.now().strftime('%Y-%m-%d')}", "="*40, f"TOTAL: {total}", f"URGENT: {urgent}", "-"*30]
        for cat in categories:
            ct = [t for t in tickets if t.category.lower() == cat]
            lines.append(f"{cat.upper()}: Open {len([t for t in ct if t.status=='open'])} | Resolved {len([t for t in ct if t.status=='resolved'])}")
        send_raw_email(SUPER_ADMIN_EMAIL, "Daily Helpdesk Summary", "\n".join(lines))

# ------------------ DECORATORS ------------------
def login_required(fn):
    if iscoroutinefunction(fn):
        @wraps(fn)
        async def async_wrapper(*args, **kwargs):
            if not session.get("user"): return redirect(url_for("auth", tab="login"))
            return await fn(*args, **kwargs)
        return async_wrapper
    else:
        @wraps(fn)
        def sync_wrapper(*args, **kwargs):
            if not session.get("user"): return redirect(url_for("auth", tab="login"))
            return fn(*args, **kwargs)
        return sync_wrapper

def agent_required(fn):
    """
    Allows access to: Agents, Managers, and Super Admins.
    """
    if iscoroutinefunction(fn):
        @wraps(fn)
        async def async_wrapper(*args, **kwargs):
            u = session.get("user")
            if not u or u.get("role") not in ['agent', 'manager', 'super_admin']: 
                return redirect(url_for("agent_login"))
            return await fn(*args, **kwargs)
        return async_wrapper
    else:
        @wraps(fn)
        def sync_wrapper(*args, **kwargs):
            u = session.get("user")
            if not u or u.get("role") not in ['agent', 'manager', 'super_admin']: 
                return redirect(url_for("agent_login"))
            return fn(*args, **kwargs)
        return sync_wrapper

# ---------------- ROUTES -------------------
@app.route("/")
def landing(): return render_template("landing.html")

@app.route("/auth")
def auth():
    tab = request.args.get("tab", "login")
    role = request.args.get("role")
    if tab == "register" and role: return redirect(url_for("register_agent" if role.lower() == "agent" else "register_employee"))
    return render_template("auth.html", tab=tab)

@app.route("/login", methods=["GET", "POST"])
def login():
    if request.method == "GET": return redirect(url_for("auth", tab="login"))
    email = (request.form.get("email") or "").strip().lower()
    password = request.form.get("password") or ""
    if not is_valid_email_domain(email): 
        flash("Restricted domain.", "error")
        return redirect(url_for("auth", tab="login"))
    user = User.query.filter_by(email=email).first()
    if not user or not user.check_password(password):
        flash("Invalid credentials.", "error")
        return redirect(url_for("auth", tab="login"))
    session["user"] = { "email": user.email, "name": user.name, "role": user.role, "employee_role": user.employee_role }
    
    # Redirect based on Role
    if user.email == SUPER_ADMIN_EMAIL or user.role == 'super_admin':
        return redirect(url_for("super_admin_dashboard"))
    elif user.role in ['manager', 'agent']:
        return redirect(url_for("agent_dashboard"))
    else:
        return redirect(url_for("employee_home"))

@app.route("/agent/login", methods=["GET", "POST"])
def agent_login():
    if request.method == "GET": return render_template("agent_login.html")
    email = (request.form.get("email") or "").strip().lower()
    password = request.form.get("password") or ""
    
    # Allow Managers and Agents to login here
    user = User.query.filter(User.email == email, User.role.in_(['agent', 'manager', 'super_admin'])).first()
    
    if not user or not user.check_password(password): return redirect(url_for("agent_login"))
    session["user"] = { "email": user.email, "name": user.name, "role": user.role }
    
    if user.role == 'super_admin' or user.email == SUPER_ADMIN_EMAIL:
        return redirect(url_for("super_admin_dashboard"))
        
    return redirect(url_for("agent_dashboard"))

@app.route("/register/employee", methods=["GET", "POST"])
def register_employee():
    if request.method == "GET": return render_template("register_employee.html")
    email = (request.form.get("email") or "").strip().lower()
    if User.query.filter_by(email=email).first(): return redirect(url_for("auth", tab="login"))
    new_user = User(email=email, name=request.form.get("name"), role="employee", employee_role=request.form.get("employee_role"), verified=AUTO_VERIFY_REGISTRATION)  # type: ignore[call-arg]
    new_user.set_password(request.form.get("password"))
    db.session.add(new_user)
    db.session.commit()
    return redirect(url_for("auth", tab="login"))

@app.route("/register/agent", methods=["GET", "POST"])
def register_agent():
    if request.method == "GET": return render_template("register_agent.html", categories=Category.query.all())
    email = (request.form.get("email") or "").strip().lower()
    if User.query.filter_by(email=email).first(): return redirect(url_for("auth", tab="login"))
    
    # Default new registrations to 'agent' unless manually changed in DB
    new_agent = User(email=email, name=request.form.get("name"), role="agent", verified=True)  # type: ignore[call-arg]
    new_agent.set_password(request.form.get("password"))
    db.session.add(new_agent)
    db.session.commit()
    db.session.add(AdminCategories(admin_id=new_agent.id, category_id=int(request.form.get("category_id"))))  # type: ignore[call-arg]
    db.session.commit()
    return redirect(url_for("agent_login"))

@app.route("/employee/home")
@login_required
def employee_home():
    user = session.get("user")
    if user.get("role") in ['manager', 'agent']:
        return redirect(url_for("agent_dashboard"))

    db_user = User.query.filter_by(email=user["email"]).first()
    if not db_user:
        flash("DB user missing.", "error")
        return redirect(url_for("logout"))

    total_tickets = Ticket.query.filter_by(created_by_id=db_user.id).count()
    open_count = Ticket.query.filter_by(created_by_id=db_user.id, status='open').count()
    inprog_count = Ticket.query.filter_by(created_by_id=db_user.id, status='in-progress').count()
    resolved_count = Ticket.query.filter_by(created_by_id=db_user.id, status='resolved').count()
    
    stats = {
        "total": total_tickets,
        "open": open_count,
        "in_progress": inprog_count,
        "resolved": resolved_count
    }

    recent = db_user.created_tickets
    recent.sort(key=lambda x: x.created_at, reverse=True)
    recent = recent[:10]

    return render_template("employee_home.html", user=user, stats=stats, recent=recent)

@app.route("/employee/history")
@login_required
def emp_history():
    user = session.get("user")
    db_user = User.query.filter_by(email=user["email"]).first()
    
    # Fetch all tickets for this user, newest first
    tickets = Ticket.query.filter_by(created_by_id=db_user.id).order_by(Ticket.created_at.desc()).all()
    
    return render_template("emp_history.html", tickets=tickets, user=user)

@app.route("/tickets/new", methods=["GET", "POST"])
@login_required
async def raise_ticket():
    user_session = session.get("user")
    db_user = User.query.filter_by(email=user_session["email"]).first()
    if request.method == "GET":
        problems = CommonProblem.query.all()
        return render_template("raise_ticket.html", user=user_session, common_problems=problems)
    
    subject = request.form.get("subject")
    description = request.form.get("description")
    
    # 1. AI Analysis
    analysis = await analyze_ticket_with_llm(subject, description)
    
    # 2. Get the Correct Category
    category_obj = Category.query.filter_by(name=analysis.category).first() or Category.query.first()
    
    # 3. ROUTING: Find the best AGENT (Least Loaded)
    assigned_agent = get_best_agent_least_loaded(category_obj)
    
    # Fallback: If no agent found, try any manager to ensure it gets saved
    if not assigned_agent:
        assigned_agent = User.query.filter(User.role.in_(['manager', 'super_admin'])).first()

    # 4. Create Ticket
    ticket_data = dict(
        subject=subject, 
        description=description, 
        priority=analysis.priority, 
        status="open", 
        category=category_obj.name, 
        category_id=category_obj.id, 
        creator=db_user, 
        assigned_admin_id=assigned_agent.id if assigned_agent else None, 
        sentiment=analysis.sentiment, 
        suggested_steps=analysis.suggested_steps
    )
    ticket = Ticket(**ticket_data)  # type: ignore[call-arg]
    db.session.add(ticket)
    db.session.commit()

    if assigned_agent and assigned_agent.email: notify_status_change(ticket, assigned_agent.email, type="new_agent")
    if db_user.email: notify_status_change(ticket, db_user.email, type="new_user")

    # --- POPUP MESSAGE ---
    admin_name = assigned_agent.name if assigned_agent else "IT Support"
    if analysis.sentiment == "Furious": 
        flash(f"Critical Priority! Assigned to {admin_name} for immediate review.", "error")
    else: 
        flash(f"Ticket Created! Successfully assigned to {admin_name}.", "success")
        
    return redirect(url_for("employee_home"))

# --- CHATBOT ROUTE ---
@app.route("/chat", methods=["POST"])
def chat():
    data = request.json
    user_message = data.get("message", "").strip()
    history = data.get("history", [])
    if not user_message: return jsonify({"response": "Say something!"})
    
    try:
        bot_reply = asyncio.run(get_chat_response(user_message, history))
        return jsonify({ "response": bot_reply["text"], "redirect": bot_reply["redirect"] })
    except Exception as e:
        print(f"Chatbot Error: {e}")
        return jsonify({"response": "System Error.", "redirect": None})

@app.route("/dashboard")
@login_required
def dashboard(): return redirect(url_for("employee_home"))

@app.route("/agent/dashboard")
@agent_required
def agent_dashboard():
    agent_session = session.get("user")
    db_agent = User.query.filter_by(email=agent_session["email"]).first()

    # ------------------ HIERARCHY LOGIC ------------------
    if db_agent.role == 'manager':
        # MANAGER VIEW: See ALL tickets in the category they manage
        # Find which category this manager belongs to
        cat_link = AdminCategories.query.filter_by(admin_id=db_agent.id).first()
        if cat_link:
            tickets = Ticket.query.filter_by(category_id=cat_link.category_id).order_by(Ticket.created_at.desc()).all()
        else:
            tickets = [] # Manager assigned to no category?
    else:
        # AGENT VIEW: See ONLY tickets assigned to them
        tickets = Ticket.query.filter_by(assigned_admin_id=db_agent.id).order_by(Ticket.created_at.desc()).all()
    # -----------------------------------------------------

    stats = {
        "total": len(tickets),
        "open": len([t for t in tickets if t.status == 'open']),
        "in_progress": len([t for t in tickets if t.status == 'in-progress']),
        "resolved": len([t for t in tickets if t.status == 'resolved']),
        "high": len([t for t in tickets if t.priority == 'high']),
        "medium": len([t for t in tickets if t.priority == 'medium']),
        "low": len([t for t in tickets if t.priority == 'low']),
    }

    return render_template("agent_dashboard.html", admin=db_agent, tickets=tickets, stats=stats)

@app.route("/super-admin")
@login_required
def super_admin_dashboard():
    if session["user"]["email"] != SUPER_ADMIN_EMAIL: return redirect(url_for("agent_dashboard"))
    tickets = Ticket.query.order_by(Ticket.created_at.desc()).all()
    stats = {
        "total": len(tickets),
        "open": len([t for t in tickets if t.status == 'open']),
        "in_progress": len([t for t in tickets if t.status == 'in-progress']),
        "resolved": len([t for t in tickets if t.status == 'resolved']),
        "software": len([t for t in tickets if t.category == 'software']),
        "hardware": len([t for t in tickets if t.category == 'hardware']),
        "network": len([t for t in tickets if t.category == 'network']),
        "database": len([t for t in tickets if t.category == 'database']),
    }
    return render_template("super_dashboard.html", admin=session["user"], tickets=tickets, stats=stats)

@app.route("/ticket/<int:ticket_id>")
@login_required
def ticket_detail(ticket_id):
    t = Ticket.query.get_or_404(ticket_id)
    return render_template("ticket_detail.html", ticket=t, user=session["user"])

@app.route("/ticket/<int:ticket_id>/update", methods=["POST"])
@agent_required
def ticket_update(ticket_id):
    t = Ticket.query.get_or_404(ticket_id)
    old_status = t.status
    new_status = request.form.get("status") or t.status
    remarks = (request.form.get("remarks") or "").strip()

    # Server-side validation
    if new_status in ['in-progress', 'resolved'] and not remarks:
        flash("Remarks are required to update status to In Progress or Resolved.", "error")
        return redirect(url_for("ticket_detail", ticket_id=ticket_id))

    t.status = new_status
    t.priority = request.form.get("priority") or t.priority
    if remarks: t.remarks = remarks # Save remarks
    
    db.session.commit()
    if old_status != t.status: notify_status_change(t, t.creator.email, type="update")
    return redirect(url_for("ticket_detail", ticket_id=ticket_id))

# ==================== FEEDBACK ROUTES ====================

@app.route("/ticket/<int:ticket_id>/feedback", methods=["POST"])
@login_required
def submit_feedback(ticket_id):
    ticket = Ticket.query.get_or_404(ticket_id)
    db_user = User.query.filter_by(email=session["user"]["email"]).first()

    # Only the ticket creator can leave feedback, and only on resolved tickets
    if ticket.created_by_id != db_user.id or ticket.status != 'resolved':
        flash("Cannot submit feedback.", "error")
        return redirect(url_for("ticket_detail", ticket_id=ticket_id))

    if ticket.feedback:
        flash("Feedback already submitted.", "info")
        return redirect(url_for("ticket_detail", ticket_id=ticket_id))

    rating = int(request.form.get("rating", 3))
    comment = (request.form.get("feedback_comment") or "").strip()
    feedback = Feedback(ticket_id=ticket_id, rating=max(1, min(5, rating)), comment=comment)  # type: ignore[call-arg]
    db.session.add(feedback)
    db.session.commit()
    flash("Thank you for your feedback!", "success")
    return redirect(url_for("ticket_detail", ticket_id=ticket_id))

@app.route("/manager/feedback")
@login_required
def manager_feedback():
    user = session.get("user")
    db_user = User.query.filter_by(email=user["email"]).first()
    if db_user.role != 'manager':
        return redirect(url_for("agent_dashboard"))

    # Find which category this manager handles
    cat_link = AdminCategories.query.filter_by(admin_id=db_user.id).first()
    if not cat_link:
        return render_template("manager_feedback.html", feedbacks=[], category="Unknown", avg_rating=0, user=user)

    category = Category.query.get(cat_link.category_id)

    # Get all feedback for tickets in this category
    feedbacks = db.session.query(Feedback).join(Ticket).filter(
        Ticket.category_id == cat_link.category_id
    ).order_by(Feedback.created_at.desc()).all()

    avg_rating = round(sum(f.rating for f in feedbacks) / len(feedbacks), 1) if feedbacks else 0

    return render_template("manager_feedback.html",
        feedbacks=feedbacks, category=category.name, avg_rating=avg_rating, user=user)

# ==================== COMMON PROBLEMS ROUTES ====================

@app.route("/super-admin/common-problems")
@login_required
def manage_common_problems():
    if session["user"]["email"] != SUPER_ADMIN_EMAIL:
        return redirect(url_for("agent_dashboard"))
    problems = CommonProblem.query.all()
    categories = Category.query.all()
    return render_template("manage_problems.html", problems=problems, categories=categories)

@app.route("/super-admin/common-problems/add", methods=["POST"])
@login_required
def add_common_problem():
    if session["user"]["email"] != SUPER_ADMIN_EMAIL:
        return redirect(url_for("agent_dashboard"))
    problem_data = dict(
        label=request.form.get("label"),
        subject=request.form.get("subject"),
        description=request.form.get("description"),
        category=request.form.get("category"),
        priority=request.form.get("priority", "medium")
    )
    problem = CommonProblem(**problem_data)  # type: ignore[call-arg]
    db.session.add(problem)
    db.session.commit()
    flash("Common problem added!", "success")
    return redirect(url_for("manage_common_problems"))

@app.route("/super-admin/common-problems/<int:pid>/delete", methods=["POST"])
@login_required
def delete_common_problem(pid):
    if session["user"]["email"] != SUPER_ADMIN_EMAIL:
        return redirect(url_for("agent_dashboard"))
    problem = CommonProblem.query.get_or_404(pid)
    db.session.delete(problem)
    db.session.commit()
    flash("Problem removed.", "success")
    return redirect(url_for("manage_common_problems"))

# ==================== AUTH & MISC ====================

@app.route("/logout")
def logout():
    session.pop("user", None)
    return redirect(url_for("landing"))

def setup_database():
    with app.app_context():
        db.create_all()
        for c in ['software', 'hardware', 'network', 'database']:
            if not Category.query.filter_by(name=c).first(): db.session.add(Category(name=c))  # type: ignore[call-arg]
        db.session.commit()

        # Seed default common problems if table is empty
        if CommonProblem.query.count() == 0:
            default_problems = [
                CommonProblem(**p) for p in [  # type: ignore[call-arg]
                    dict(label="WiFi Not Connecting", subject="WiFi is not connecting", description="I am unable to connect to the office WiFi network. I have tried restarting my device but the issue persists.", category="network", priority="medium"),
                    dict(label="VPN Keeps Disconnecting", subject="VPN keeps disconnecting", description="The VPN connection drops frequently during work sessions, disrupting my workflow.", category="network", priority="high"),
                    dict(label="Laptop Running Slow", subject="Laptop is very slow", description="My laptop takes a very long time to open applications and respond to commands.", category="hardware", priority="medium"),
                    dict(label="Printer Not Working", subject="Printer not responding", description="I cannot print documents. The printer shows offline status even though it is powered on.", category="hardware", priority="low"),
                    dict(label="Software Installation Request", subject="Need software installed", description="I need a new software application installed on my workstation for work purposes.", category="software", priority="low"),
                    dict(label="Password Reset Issue", subject="Cannot reset my password", description="The password reset portal is not working for me. It shows an error when I try to reset.", category="software", priority="medium"),
                    dict(label="Email Not Syncing", subject="Outlook email not syncing", description="My Outlook is not receiving or sending new emails. The sync seems to be stuck.", category="software", priority="high"),
                    dict(label="Database Access Denied", subject="Cannot access the database", description="Getting 'access denied' error when trying to connect to the database server.", category="database", priority="medium"),
                ]
            ]
            for p in default_problems:
                db.session.add(p)
            db.session.commit()
            print("Seeded 8 default common problems.")

if __name__ == "__main__":
    setup_database()
    scheduler = BackgroundScheduler()
    scheduler.add_job(func=generate_daily_report, trigger="cron", hour=18, minute=0)
    scheduler.start()
    atexit.register(lambda: scheduler.shutdown())
    app.run(debug=True, port=5000, use_reloader=False)
