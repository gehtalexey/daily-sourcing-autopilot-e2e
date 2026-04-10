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

    blocks = [
        {
            "type": "header",
            "text": {"type": "plain_text", "text": f"Daily Sourcing Report — {position_id}"}
        },
        {
            "type": "context",
            "elements": [{"type": "mrkdwn", "text": f"*{today}*"}]
        },
        {"type": "divider"},
    ]

    # === TODAY'S RUN ===
    blocks.append({
        "type": "section",
        "text": {"type": "mrkdwn", "text": "*Today's Run*"}
    })

    today_fields = [
        {"type": "mrkdwn", "text": f"*Searched:*\n{t.get('searched', 0)}"},
        {"type": "mrkdwn", "text": f"*Qualified:*\n{t.get('qualified', 0)}"},
        {"type": "mrkdwn", "text": f"*Rejected:*\n{t.get('not_qualified', 0)}"},
    ]
    blocks.append({"type": "section", "fields": today_fields})

    # Today's search by source
    by_source = t.get('by_source', {})
    if by_source:
        source_lines = [f"  {variant}: {count}" for variant, count in sorted(by_source.items(), key=lambda x: -x[1])]
        blocks.append({
            "type": "section",
            "text": {"type": "mrkdwn", "text": "*By Search:*\n" + "\n".join(source_lines)}
        })

    blocks.append({"type": "divider"})

    # === ALL-TIME STATS ===
    blocks.append({
        "type": "section",
        "text": {"type": "mrkdwn", "text": "*All-Time Totals*"}
    })

    alltime_fields = [
        {"type": "mrkdwn", "text": f"*Total Sourced:*\n{a.get('total_sourced', 0)}"},
        {"type": "mrkdwn", "text": f"*Qualified:*\n{a.get('qualified', 0)}"},
        {"type": "mrkdwn", "text": f"*With Email:*\n{a.get('with_email', 0)}"},
        {"type": "mrkdwn", "text": f"*Pushed to GEM:*\n{a.get('pushed_to_gem', 0)}"},
    ]
    blocks.append({"type": "section", "fields": alltime_fields})

    pending = a.get('pending_screening', 0)
    not_pushed = a.get('not_pushed', 0)
    if pending or not_pushed:
        extra_fields = []
        if pending:
            extra_fields.append({"type": "mrkdwn", "text": f"*Pending Screen:*\n{pending}"})
        if not_pushed:
            extra_fields.append({"type": "mrkdwn", "text": f"*Not Pushed:*\n{not_pushed}"})
        blocks.append({"type": "section", "fields": extra_fields})

    blocks.append({"type": "divider"})

    # === QUALIFICATION RATES ===
    qual_rates = stats.get('qual_rates', {})
    if qual_rates:
        blocks.append({
            "type": "section",
            "text": {"type": "mrkdwn", "text": "*Qualification Rates by Search*"}
        })
        rate_lines = []
        for variant, info in sorted(qual_rates.items(), key=lambda x: -int(x[1].get('rate', '0%').rstrip('%'))):
            rate_lines.append(
                f"  {variant}: *{info['rate']}* ({info['qualified']}/{info['screened']} screened, {info['total']} total)"
            )
        blocks.append({
            "type": "section",
            "text": {"type": "mrkdwn", "text": "\n".join(rate_lines)}
        })

        blocks.append({"type": "divider"})

    # === ISSUES ===
    issue_lines = []
    if issues.get('missing_openers_count', 0) > 0:
        names = ', '.join(issues['missing_openers'][:5])
        issue_lines.append(f":warning: {issues['missing_openers_count']} qualified without opener: {names}")
    if issues.get('not_pushed_to_gem', 0) > 0:
        issue_lines.append(f":warning: {issues['not_pushed_to_gem']} qualified not pushed to GEM")

    if issue_lines:
        blocks.append({
            "type": "section",
            "text": {"type": "mrkdwn", "text": "*Issues*\n" + "\n".join(issue_lines)}
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
