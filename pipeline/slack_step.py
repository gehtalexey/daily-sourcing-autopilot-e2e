"""
Slack Step — Send pipeline notifications to Slack channel.

Usage:
    python -m pipeline.slack_step <position_id> [run_id]       → End-of-run report
    python -m pipeline.slack_step start <position_id>          → Start notification

Prints JSON with message_ts to stdout.
"""

import sys
import json
from datetime import datetime, timezone
from pathlib import Path

import requests

from core.db import get_supabase_client
from pipeline.controller import get_full_stats


def log(msg):
    print(f"[slack] {msg}", file=sys.stderr)


def get_slack_config() -> dict:
    """Load Slack config from config.json."""
    try:
        config_path = Path(__file__).parent.parent / 'config.json'
        if config_path.exists():
            with open(config_path) as f:
                config = json.load(f)
                return {
                    'bot_token': config.get('slack_bot_token', ''),
                    'channel': config.get('slack_channel', ''),
                }
    except Exception:
        pass
    return {}


def send_slack_message(token: str, channel: str, text: str, blocks: list = None) -> dict:
    """Send a message to Slack."""
    payload = {
        'channel': channel,
        'text': text,
    }
    if blocks:
        payload['blocks'] = blocks

    response = requests.post(
        'https://slack.com/api/chat.postMessage',
        headers={
            'Authorization': f'Bearer {token}',
            'Content-Type': 'application/json',
        },
        json=payload,
        timeout=30,
    )

    data = response.json()
    if not data.get('ok'):
        return {'error': data.get('error', 'Unknown Slack error')}
    return {'ok': True, 'ts': data.get('ts')}


# =============================================================================
# START NOTIFICATION
# =============================================================================

def build_start_blocks(position_id: str) -> list:
    """Build a simple start notification."""
    pos_display = position_id.replace('-', ' ').title()
    now = datetime.now(timezone.utc).strftime('%H:%M UTC')

    return [
        {
            "type": "section",
            "text": {"type": "mrkdwn", "text": f":rocket: *Pipeline started* — *{pos_display}*\nKicked off at {now}. Will report results when done."}
        },
    ]


# =============================================================================
# END-OF-RUN REPORT
# =============================================================================

def build_report_blocks(stats: dict) -> list:
    """Build the end-of-run Slack report."""
    position_id = stats.get('position_id', '?')
    today = stats.get('run_date', datetime.now(timezone.utc).strftime('%Y-%m-%d'))
    t = stats.get('today', {})
    a = stats.get('all_time', {})
    issues = stats.get('issues', {})
    pos_display = position_id.replace('-', ' ').title()

    blocks = [
        {
            "type": "header",
            "text": {"type": "plain_text", "text": f"{pos_display} — Run Complete"}
        },
        {
            "type": "context",
            "elements": [{"type": "mrkdwn", "text": f":calendar: {today}"}]
        },
        {"type": "divider"},
    ]

    # ── TODAY'S RUN ──────────────────────────────────────────────
    t_searched = t.get('searched', 0)
    t_screened = t.get('screened', 0)
    t_qualified = t.get('qualified', 0)
    t_rejected = t.get('not_qualified', 0)
    t_pushed = t.get('pushed_to_gem', 0)

    blocks.append({
        "type": "section",
        "text": {"type": "mrkdwn", "text": ":zap: *What happened today*"}
    })

    today_lines = []
    if t_searched > 0:
        today_lines.append(f":mag_right:  Found *{t_searched}* new candidates")
    if t_screened > 0:
        t_rate = f"{t_qualified/t_screened*100:.0f}%" if t_screened > 0 else "—"
        today_lines.append(f":clipboard:  Screened *{t_screened}* → *{t_qualified}* qualified, *{t_rejected}* rejected  ({t_rate} pass rate)")
    if t_pushed > 0:
        today_lines.append(f":gem:  Pushed *{t_pushed}* to GEM")
    if not today_lines:
        today_lines.append(":zzz:  No pipeline activity today")

    blocks.append({
        "type": "section",
        "text": {"type": "mrkdwn", "text": "\n".join(today_lines)}
    })

    # By search source (today only)
    by_source = t.get('by_source', {})
    if by_source:
        source_parts = [f"{v}: {c}" for v, c in sorted(by_source.items(), key=lambda x: -x[1])]
        blocks.append({
            "type": "context",
            "elements": [{"type": "mrkdwn", "text": f"Search breakdown: {', '.join(source_parts)}"}]
        })

    blocks.append({"type": "divider"})

    # ── PIPELINE TOTALS ─────────────────────────────────────────
    total = a.get('total_sourced', 0)
    qual = a.get('qualified', 0)
    email = a.get('with_email', 0)
    gem = a.get('pushed_to_gem', 0)
    pending = a.get('pending_screening', 0)
    not_pushed = a.get('not_pushed', 0)

    blocks.append({
        "type": "section",
        "text": {"type": "mrkdwn", "text": ":bar_chart: *Pipeline totals (all time)*"}
    })

    blocks.append({
        "type": "section",
        "fields": [
            {"type": "mrkdwn", "text": f"*Sourced*\n{total}"},
            {"type": "mrkdwn", "text": f"*Screened*\n{total - pending}"},
            {"type": "mrkdwn", "text": f"*Qualified*\n{qual}  ({qual/(total - pending)*100:.0f}% rate)" if (total - pending) > 0 else f"*Qualified*\n{qual}"},
            {"type": "mrkdwn", "text": f"*In GEM*\n{gem}"},
        ]
    })

    blocks.append({
        "type": "section",
        "fields": [
            {"type": "mrkdwn", "text": f"*With Email*\n{email}  ({email/qual*100:.0f}%)" if qual > 0 else f"*With Email*\n{email}"},
            {"type": "mrkdwn", "text": f"*Pending Screen*\n{pending}"},
        ]
    })

    # Action items (if any)
    action_items = []
    if pending > 0:
        action_items.append(f":hourglass_flowing_sand: {pending} still need screening")
    if not_pushed > 0:
        action_items.append(f":warning: {not_pushed} qualified but not yet in GEM")
    if issues.get('missing_openers_count', 0) > 0:
        action_items.append(f":warning: {issues['missing_openers_count']} missing openers")

    if action_items:
        blocks.append({"type": "divider"})
        blocks.append({
            "type": "section",
            "text": {"type": "mrkdwn", "text": ":rotating_light: *Action needed*\n" + "\n".join(action_items)}
        })

    # ── QUAL RATES BY SEARCH ────────────────────────────────────
    qual_rates = stats.get('qual_rates', {})
    if qual_rates:
        blocks.append({"type": "divider"})
        blocks.append({
            "type": "section",
            "text": {"type": "mrkdwn", "text": ":dart: *Qual rate by search variant*"}
        })
        rate_lines = []
        for variant, info in sorted(qual_rates.items(), key=lambda x: -int(x[1].get('rate', '0%').rstrip('%'))):
            pct = int(info['rate'].rstrip('%'))
            bar_len = min(pct // 5, 20)
            bar = '█' * bar_len + '░' * (20 - bar_len)
            rate_lines.append(
                f"`{bar}` *{info['rate']}*  {variant}  ({info['qualified']}/{info['screened']})"
            )
        blocks.append({
            "type": "section",
            "text": {"type": "mrkdwn", "text": "\n".join(rate_lines)}
        })

    return blocks


# =============================================================================
# CLI
# =============================================================================

def main():
    if len(sys.argv) < 2:
        print("Usage: python -m pipeline.slack_step <position_id> [run_id]", file=sys.stderr)
        print("       python -m pipeline.slack_step start <position_id>", file=sys.stderr)
        sys.exit(1)

    slack_config = get_slack_config()
    if not slack_config.get('bot_token') or not slack_config.get('channel'):
        log("Slack not configured (missing bot_token or channel)")
        print(json.dumps({"error": "Slack not configured"}))
        return

    # ── START notification ──
    if sys.argv[1] == 'start':
        if len(sys.argv) < 3:
            print("Usage: python -m pipeline.slack_step start <position_id>", file=sys.stderr)
            sys.exit(1)
        position_id = sys.argv[2]
        pos_display = position_id.replace('-', ' ').title()
        blocks = build_start_blocks(position_id)
        result = send_slack_message(
            slack_config['bot_token'],
            slack_config['channel'],
            f"Pipeline started — {pos_display}",
            blocks,
        )
        if result.get('error'):
            log(f"Slack error: {result['error']}")
        else:
            log(f"Start notification sent: {result.get('ts')}")
        print(json.dumps(result))
        return

    # ── END-OF-RUN report ──
    position_id = sys.argv[1]
    run_id = sys.argv[2] if len(sys.argv) > 2 else None

    # Also accept stats from stdin (backward compat)
    stdin_stats = {}
    if not sys.stdin.isatty():
        try:
            raw = sys.stdin.read()
            if raw.strip():
                stdin_stats = json.loads(raw)
        except Exception:
            pass

    client = get_supabase_client()
    if client:
        stats = get_full_stats(client, position_id, run_id)
    else:
        stats = stdin_stats

    blocks = build_report_blocks(stats)

    t = stats.get('today', {})
    a = stats.get('all_time', {})
    fallback_text = (
        f"Run complete — {position_id}: "
        f"{t.get('screened', 0)} screened, "
        f"{t.get('qualified', 0)} qualified today, "
        f"{a.get('pushed_to_gem', 0)} total in GEM"
    )

    result = send_slack_message(
        slack_config['bot_token'],
        slack_config['channel'],
        fallback_text,
        blocks,
    )

    if result.get('error'):
        log(f"Slack error: {result['error']}")
    else:
        log(f"Slack message sent: {result.get('ts')}")

    print(json.dumps(result))


if __name__ == '__main__':
    main()
