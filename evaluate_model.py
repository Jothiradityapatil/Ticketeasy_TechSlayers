"""
TicketEasy AI Model Evaluation Script
=====================================
Tests the NVIDIA NIM LLM pipeline against a manually labeled dataset
and generates accuracy metrics for the project report.

Run: python evaluate_model.py
"""

import asyncio
import json
import os
from dotenv import load_dotenv

load_dotenv()

from ai_engine import analyze_ticket_with_llm

# ============================================================
# TEST DATASET - Manually labeled tickets with expected values
# ============================================================
TEST_TICKETS = [
    # --- HARDWARE ---
    {"subject": "Laptop screen flickering", "description": "My laptop display keeps flickering randomly during work.", "expected_category": "hardware", "expected_sentiment": "Neutral"},
    {"subject": "Mouse not working", "description": "My wireless mouse stopped responding after I changed batteries.", "expected_category": "hardware", "expected_sentiment": "Neutral"},
    {"subject": "Keyboard keys stuck", "description": "Several keys on my keyboard are stuck and I can't type properly. This is so frustrating!", "expected_category": "hardware", "expected_sentiment": "Frustrated"},
    {"subject": "Printer offline", "description": "The office printer shows offline status but it is powered on and connected.", "expected_category": "hardware", "expected_sentiment": "Confused"},
    {"subject": "Laptop overheating badly", "description": "My laptop gets extremely hot within 10 minutes and shuts down. I've lost work twice today!", "expected_category": "hardware", "expected_sentiment": "Furious"},
    {"subject": "Monitor not displaying", "description": "Connected my monitor but it just shows a black screen. Everything was fine yesterday.", "expected_category": "hardware", "expected_sentiment": "Confused"},
    {"subject": "Headset microphone broken", "description": "My headset mic stopped working during an important call. Very unprofessional.", "expected_category": "hardware", "expected_sentiment": "Frustrated"},

    # --- SOFTWARE ---
    {"subject": "SAP crashes on login", "description": "SAP application throws a runtime error every time I try to log in.", "expected_category": "software", "expected_sentiment": "Furious"},
    {"subject": "Excel not opening files", "description": "Microsoft Excel crashes when I try to open any .xlsx file.", "expected_category": "software", "expected_sentiment": "Frustrated"},
    {"subject": "Cannot install updates", "description": "Windows update has been stuck at 45% for the last 3 hours.", "expected_category": "software", "expected_sentiment": "Frustrated"},
    {"subject": "Password reset not working", "description": "I tried resetting my password through the portal but it says 'invalid token' every time.", "expected_category": "software", "expected_sentiment": "Confused"},
    {"subject": "Outlook keeps freezing", "description": "Outlook hangs for 30 seconds every time I open it. Please fix this.", "expected_category": "software", "expected_sentiment": "Frustrated"},
    {"subject": "Application updated successfully", "description": "Just wanted to confirm that the CRM update was installed without issues. Thanks for the help!", "expected_category": "software", "expected_sentiment": "Grateful"},
    {"subject": "Need Zoom installed", "description": "Please install Zoom on my workstation for upcoming virtual meetings.", "expected_category": "software", "expected_sentiment": "Professional"},

    # --- NETWORK ---
    {"subject": "WiFi not connecting", "description": "Cannot connect to the office WiFi network since this morning.", "expected_category": "network", "expected_sentiment": "Frustrated"},
    {"subject": "VPN keeps disconnecting", "description": "The VPN drops every 10 minutes during video calls. I have a client presentation in 1 hour!", "expected_category": "network", "expected_sentiment": "Urgent"},
    {"subject": "Slow internet speed", "description": "Internet speed is extremely slow today. Pages take forever to load.", "expected_category": "network", "expected_sentiment": "Frustrated"},
    {"subject": "Cannot access shared drive", "description": "Getting 'network path not found' when trying to access the shared drive.", "expected_category": "network", "expected_sentiment": "Confused"},
    {"subject": "Network down entire floor", "description": "The entire 3rd floor has no network connectivity. 50 people cannot work. This is unacceptable!", "expected_category": "network", "expected_sentiment": "Furious"},
    {"subject": "Internet restored", "description": "The internet is back up and working fine now. Thank you for the quick fix!", "expected_category": "network", "expected_sentiment": "Grateful"},
    {"subject": "Firewall blocking application", "description": "The firewall seems to be blocking our new project management tool. Can you whitelist it?", "expected_category": "network", "expected_sentiment": "Professional"},

    # --- DATABASE ---
    {"subject": "Database backup failed", "description": "Scheduled nightly backup failed with error code 1053. Need urgent attention.", "expected_category": "database", "expected_sentiment": "Urgent"},
    {"subject": "SQL query returning wrong results", "description": "The monthly report query is returning duplicate rows. Data looks incorrect.", "expected_category": "database", "expected_sentiment": "Confused"},
    {"subject": "Database connection timeout", "description": "Application keeps losing connection to the database server every few minutes.", "expected_category": "database", "expected_sentiment": "Frustrated"},
    {"subject": "Cannot access database", "description": "Getting 'access denied' when trying to connect to the production database.", "expected_category": "database", "expected_sentiment": "Neutral"},
    {"subject": "Data migration completed", "description": "The data migration from old system to new database was completed successfully. All records verified.", "expected_category": "database", "expected_sentiment": "Grateful"},
    {"subject": "Database is extremely slow", "description": "Queries that used to take 2 seconds now take 30 seconds. This is killing our productivity!", "expected_category": "database", "expected_sentiment": "Furious"},

    # --- EDGE CASES (harder for the model) ---
    {"subject": "Nothing works anymore", "description": "My entire setup is broken. Computer won't start, can't connect to anything. I give up.", "expected_category": "hardware", "expected_sentiment": "Sad"},
    {"subject": "Oh great, another outage", "description": "Wonderful, the email server is down again. Third time this week. Truly impressive.", "expected_category": "software", "expected_sentiment": "Sarcastic"},
    {"subject": "Requesting new workstation", "description": "I would like to request a new workstation as my current one is 5 years old and quite slow.", "expected_category": "hardware", "expected_sentiment": "Professional"},
    {"subject": "Happy with the new system", "description": "The new ticketing system is great! Much faster than the old one. Good job team!", "expected_category": "software", "expected_sentiment": "Happy"},
]


async def run_evaluation():
    print("=" * 70)
    print("  TicketEasy AI Model Evaluation")
    print("=" * 70)
    print(f"\nTotal test tickets: {len(TEST_TICKETS)}")
    print("Running AI analysis on each ticket...\n")

    results = []
    category_correct = 0
    sentiment_correct = 0
    # Track per-category and per-sentiment accuracy
    cat_stats = {}   # {category: {"total": n, "correct": n}}
    sent_stats = {}  # {sentiment: {"total": n, "correct": n}}

    for i, ticket in enumerate(TEST_TICKETS, 1):
        print(f"  [{i}/{len(TEST_TICKETS)}] Analyzing: {ticket['subject'][:50]}...", end=" ")

        try:
            result = await analyze_ticket_with_llm(ticket["subject"], ticket["description"])

            cat_match = result.category.lower() == ticket["expected_category"].lower()
            sent_match = result.sentiment.lower() == ticket["expected_sentiment"].lower()

            if cat_match:
                category_correct += 1
            if sent_match:
                sentiment_correct += 1

            # Per-category stats
            exp_cat = ticket["expected_category"]
            if exp_cat not in cat_stats:
                cat_stats[exp_cat] = {"total": 0, "correct": 0}
            cat_stats[exp_cat]["total"] += 1
            if cat_match:
                cat_stats[exp_cat]["correct"] += 1

            # Per-sentiment stats
            exp_sent = ticket["expected_sentiment"]
            if exp_sent not in sent_stats:
                sent_stats[exp_sent] = {"total": 0, "correct": 0}
            sent_stats[exp_sent]["total"] += 1
            if sent_match:
                sent_stats[exp_sent]["correct"] += 1

            status = "PASS" if (cat_match and sent_match) else "PARTIAL" if (cat_match or sent_match) else "FAIL"
            symbol = "OK" if status == "PASS" else "~~" if status == "PARTIAL" else "XX"
            print(f"[{symbol}]")

            results.append({
                "subject": ticket["subject"],
                "expected_category": ticket["expected_category"],
                "predicted_category": result.category,
                "category_match": cat_match,
                "expected_sentiment": ticket["expected_sentiment"],
                "predicted_sentiment": result.sentiment,
                "sentiment_match": sent_match,
                "predicted_priority": result.priority,
            })

        except Exception as e:
            print(f"[ERROR: {e}]")
            results.append({
                "subject": ticket["subject"],
                "error": str(e),
            })

    # ============================================================
    # RESULTS SUMMARY
    # ============================================================
    total = len(TEST_TICKETS)
    cat_acc = (category_correct / total) * 100
    sent_acc = (sentiment_correct / total) * 100
    both_correct = sum(1 for r in results if r.get("category_match") and r.get("sentiment_match"))
    overall_acc = (both_correct / total) * 100

    print("\n" + "=" * 70)
    print("  RESULTS SUMMARY")
    print("=" * 70)

    print(f"\n  Category Classification Accuracy:  {category_correct}/{total}  ({cat_acc:.1f}%)")
    print(f"  Sentiment Detection Accuracy:      {sentiment_correct}/{total}  ({sent_acc:.1f}%)")
    print(f"  Both Correct (Overall):            {both_correct}/{total}  ({overall_acc:.1f}%)")

    # Per-category breakdown
    print(f"\n  {'Category':<12} {'Correct':<10} {'Total':<8} {'Accuracy':<10}")
    print("  " + "-" * 40)
    for cat in ["hardware", "software", "network", "database"]:
        if cat in cat_stats:
            s = cat_stats[cat]
            acc = (s["correct"] / s["total"]) * 100
            print(f"  {cat:<12} {s['correct']:<10} {s['total']:<8} {acc:.1f}%")

    # Per-sentiment breakdown
    print(f"\n  {'Sentiment':<14} {'Correct':<10} {'Total':<8} {'Accuracy':<10}")
    print("  " + "-" * 42)
    for sent, s in sorted(sent_stats.items()):
        acc = (s["correct"] / s["total"]) * 100
        print(f"  {sent:<14} {s['correct']:<10} {s['total']:<8} {acc:.1f}%")

    # Misclassifications
    mismatches = [r for r in results if not r.get("category_match") or not r.get("sentiment_match")]
    if mismatches:
        print(f"\n  MISCLASSIFICATIONS ({len(mismatches)}):")
        print("  " + "-" * 60)
        for r in mismatches:
            if "error" in r:
                print(f"  {r['subject'][:40]:<40} ERROR")
                continue
            issues = []
            if not r["category_match"]:
                issues.append(f"Cat: {r['expected_category']}->{r['predicted_category']}")
            if not r["sentiment_match"]:
                issues.append(f"Sent: {r['expected_sentiment']}->{r['predicted_sentiment']}")
            print(f"  {r['subject'][:40]:<40} {', '.join(issues)}")

    # Save detailed results to JSON
    output = {
        "total_tickets": total,
        "category_accuracy": f"{cat_acc:.1f}%",
        "sentiment_accuracy": f"{sent_acc:.1f}%",
        "overall_accuracy": f"{overall_acc:.1f}%",
        "detailed_results": results,
    }
    with open("evaluation_results.json", "w") as f:
        json.dump(output, f, indent=2)

    print(f"\n  Detailed results saved to: evaluation_results.json")
    print("=" * 70)
    print("\n  Use these numbers in Chapter 10 of your report!")
    print("=" * 70)


if __name__ == "__main__":
    asyncio.run(run_evaluation())
