"""
Slack Step — Send detailed pipeline summary to Slack channel.

Usage:
    python -m pipeline.slack_step <position_id> [run_id]

Aggregates full pipeline statistics and sends a detailed Block Kit report.
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


def build_detailed_blocks(stats: dict) -> list:
    """Build detailed Slack Block Kit blocks from full pipeline stats."""
    position_id = stats.get('position_id', '?')
    today = stats.get('run_date', datetime.now(timezone.utc).strftime('%Y-%m-%d'))
    t = stats.get('today', {})
    a = stats.get('all_time', {})
    issues = stats.get('issues', {})

    # Format position name nicely
    pos_display = position_id.replace('-', ' ').title()

    blocks = [
        {
            "type": "header",
            "text": {"type": "plain_text", "text": f":mag: {pos_display} — Daily Report"}
        },
        {
            "type": "context",
            "elements": [{"type": "mrkdwn", "text": f":calendar: *{today}*"}]
        },
        {"type": "divider"},
    ]

    # === TODAY'S ACTIVITY ===
    t_searched = t.get('searched', 0)
    t_screened = t.get('screened', 0)
    t_qualified = t.get('qualified', 0)
    t_rejected = t.get('not_qualified', 0)
    t_pushed = t.get('pushed_to_gem', 0)
    t_qual_rate = f"{t_qualified/t_screened*100:.0f}%" if t_screened > 0 else "—"

    today_text = f"*:zap: Today's Activity*\n"
    today_lines = []
    if t_searched > 0:
        today_lines.append(f":mag_right: *{t_searched}* new candidates found")
    if t_screened > 0:
        today_lines.append(f":clipboard: *{t_screened}* screened → *{t_qualified}* qualified, *{t_rejected}* rejected ({t_qual_rate} pass rate)")
    if t_pushed > 0:
        today_lines.append(f":gem: *{t_pushed}* pushed to GEM")
    if not today_lines:
        today_lines.append("No pipeline activity today")

    blocks.append({
        "type": "section",
        "text": {"type": "mrkdwn", "text": today_text + "\n".join(today_lines)}
    })

    # Today's search by source
    by_source = t.get('by_source', {})
    if by_source:
        source_lines = [f"  • {variant}: {count}" for variant, count in sorted(by_source.items(), key=lambda x: -x[1])]
        blocks.append({
            "type": "context",
            "elements": [{"type": "mrkdwn", "text": "*By search:* " + ", ".join(f"{v}: {c}" for v, c in sorted(by_source.items(), key=lambda x: -x[1]))}]
        })

    blocks.append({"type": "divider"})

    # === PIPELINE TOTALS ===
    total = a.get('total_sourced', 0)
    qual = a.get('qualified', 0)
    email = a.get('with_email', 0)
    gem = a.get('pushed_to_gem', 0)
    pending = a.get('pending_screening', 0)
    not_pushed = a.get('not_pushed', 0)
    overall_rate = f"{qual/total*100:.0f}%" if total > 0 else "—"
    email_rate = f"{email/qual*100:.0f}%" if qual > 0 else "—"

    blocks.append({
        "type": "section",
        "text": {"type": "mrkdwn", "text": "*:bar_chart: Pipeline Totals*"}
    })

    blocks.append({
        "type": "section",
        "fields": [
            {"type": "mrkdwn", "text": f":busts_in_silhouette: *Sourced*\n{total}"},
            {"type": "mrkdwn", "text": f":white_check_mark: *Qualified*\n{qual} ({overall_rate})"},
            {"type": "mrkdwn", "text": f":email: *With Email*\n{email} ({email_rate})"},
            {"type": "mrkdwn", "text": f":gem: *In GEM*\n{gem}"},
        ]
    })

    # Pending / action items
    action_items = []
    if pending > 0:
        action_items.append(f":hourglass_flowing_sand: {pending} pending screening")
    if not_pushed > 0:
        action_items.append(f":warning: {not_pushed} qualified not in GEM")
    if issues.get('missing_openers_count', 0) > 0:
        action_items.append(f":warning: {issues['missing_openers_count']} qualified without opener")

    if action_items:
        blocks.append({
            "type": "context",
            "elements": [{"type": "mrkdwn", "text": " | ".join(action_items)}]
        })

    blocks.append({"type": "divider"})

    # === QUALIFICATION RATES BY SEARCH ===
    qual_rates = stats.get('qual_rates', {})
    if qual_rates:
        blocks.append({
            "type": "section",
            "text": {"type": "mrkdwn", "text": "*:dart: Qual Rate by Search*"}
        })
        rate_lines = []
        for variant, info in sorted(qual_rates.items(), key=lambda x: -int(x[1].get('rate', '0%').rstrip('%'))):
            bar_len = min(int(int(info['rate'].rstrip('%')) / 5), 20)
            bar = '█' * bar_len + '░' * (20 - bar_len)
            rate_lines.append(
                f"`{bar}` *{info['rate']}* {variant} ({info['qualified']}/{info['screened']})"
            )
        blocks.append({
            "type": "section",
            "text": {"type": "mrkdwn", "text": "\n".join(rate_lines)}
        })

    return blocks


def main():
    if len(sys.argv) < 2:
        print("Usage: python -m pipeline.slack_step <position_id> [run_id]", file=sys.stderr)
        sys.exit(1)

    position_id = sys.argv[1]
    run_id = sys.argv[2] if len(sys.argv) > 2 else None

    # Also accept stats from stdin (backward compat with orchestrator piping)
    stdin_stats = {}
    if not sys.stdin.isatty():
        try:
            raw = sys.stdin.read()
            if raw.strip():
                stdin_stats = json.loads(raw)
        except Exception:
            pass

    slack_config = get_slack_config()
    if not slack_config.get('bot_token') or not slack_config.get('channel'):
        log("Slack not configured (missing bot_token or channel)")
        print(json.dumps({"error": "Slack not configured"}))
        return

    # Get full stats from DB
    client = get_supabase_client()
    if client:
        stats = get_full_stats(client, position_id, run_id)
    else:
        stats = stdin_stats

    # Build and send message
    blocks = build_detailed_blocks(stats)

    t = stats.get('today', {})
    a = stats.get('all_time', {})
    fallback_text = (
        f"Daily Sourcing — {position_id}: "
        f"{t.get('searched', 0)} new, "
        f"{a.get('qualified', 0)} total qualified, "
        f"{a.get('pushed_to_gem', 0)} in GEM"
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
