"""
Slack Step — Send pipeline summary to Slack channel.

Usage:
    python -m pipeline.slack_step <position_id> [run_id]

Reads pipeline stats and sends a formatted summary message.
Prints JSON with message_ts to stdout.
"""

import sys
import json
from datetime import datetime
from pathlib import Path

import requests

from core.db import (
    get_supabase_client,
    get_pipeline_position,
    get_pipeline_candidates,
)


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


def build_summary_blocks(position_id: str, stats: dict) -> list:
    """Build Slack Block Kit blocks for the pipeline summary."""
    today = datetime.utcnow().strftime('%Y-%m-%d')

    blocks = [
        {
            "type": "header",
            "text": {
                "type": "plain_text",
                "text": f"Daily Sourcing Report - {position_id}",
            }
        },
        {
            "type": "context",
            "elements": [
                {"type": "mrkdwn", "text": f"*Date:* {today}"}
            ]
        },
        {"type": "divider"},
        {
            "type": "section",
            "fields": [
                {"type": "mrkdwn", "text": f"*New Today:*\n{stats.get('searched_today', 0)}"},
                {"type": "mrkdwn", "text": f"*Total Pipeline:*\n{stats.get('total_candidates', 0)}"},
                {"type": "mrkdwn", "text": f"*Qualified:*\n{stats.get('qualified', 0)}"},
                {"type": "mrkdwn", "text": f"*Not Qualified:*\n{stats.get('not_qualified', 0)}"},
            ]
        },
        {
            "type": "section",
            "fields": [
                {"type": "mrkdwn", "text": f"*With Email:*\n{stats.get('with_email', 0)}"},
                {"type": "mrkdwn", "text": f"*Pushed to GEM:*\n{stats.get('pushed_to_gem', 0)}"},
            ]
        },
    ]

    # Add step-level details if available
    step_details = []
    if stats.get('search'):
        s = stats['search']
        step_details.append(f"Search: {s.get('found', 0)} found, {s.get('saved', 0)} new")
    if stats.get('pre_filter'):
        pf = stats['pre_filter']
        step_details.append(f"Pre-filter: {pf.get('filtered_out', 0)} removed")
    if stats.get('enrich'):
        e = stats['enrich']
        step_details.append(f"Enrich: {e.get('enriched_new', 0)} new, {e.get('from_cache', 0)} cached")
    if stats.get('screen'):
        sc = stats['screen']
        step_details.append(f"Screen: {sc.get('qualified', 0)} qualified / {sc.get('not_qualified', 0)} rejected")
    if stats.get('email'):
        em = stats['email']
        step_details.append(f"Email: {em.get('found', 0)}/{em.get('looked_up', 0)} found")
    if stats.get('gem'):
        g = stats['gem']
        step_details.append(f"GEM: {g.get('pushed', 0)} pushed")

    if step_details:
        blocks.append({"type": "divider"})
        blocks.append({
            "type": "section",
            "text": {
                "type": "mrkdwn",
                "text": "*Step Details:*\n" + "\n".join(f"  {d}" for d in step_details),
            }
        })

    # Add error info if any
    if stats.get('error'):
        blocks.append({
            "type": "section",
            "text": {
                "type": "mrkdwn",
                "text": f":warning: *Error:* {stats['error']}"
            }
        })

    return blocks


def main():
    if len(sys.argv) < 2:
        print("Usage: python -m pipeline.slack_step <position_id> [stats_json]", file=sys.stderr)
        sys.exit(1)

    position_id = sys.argv[1]

    # Read stats from stdin if piped, otherwise aggregate from DB
    stats = {}
    if not sys.stdin.isatty():
        try:
            raw = sys.stdin.read()
            if raw.strip():
                stats = json.loads(raw)
        except Exception:
            pass

    slack_config = get_slack_config()
    if not slack_config.get('bot_token') or not slack_config.get('channel'):
        log("Slack not configured (missing bot_token or channel)")
        print(json.dumps({"error": "Slack not configured"}))
        return

    # If no stats provided, aggregate from DB
    if not stats.get('total_candidates'):
        client = get_supabase_client()
        if client:
            all_candidates = get_pipeline_candidates(client, position_id)
            today = datetime.utcnow().strftime('%Y-%m-%d')
            today_candidates = [c for c in all_candidates if c.get('search_run_date') == today]

            stats.update({
                "searched_today": len(today_candidates),
                "total_candidates": len(all_candidates),
                "qualified": len([c for c in all_candidates if c.get('screening_result') == 'qualified']),
                "not_qualified": len([c for c in all_candidates if c.get('screening_result') == 'not_qualified']),
                "with_email": len([c for c in all_candidates if c.get('personal_email')]),
                "pushed_to_gem": len([c for c in all_candidates if c.get('gem_pushed')]),
            })

    # Build and send message
    blocks = build_summary_blocks(position_id, stats)
    fallback_text = (
        f"Daily Sourcing Report - {position_id}: "
        f"{stats.get('searched_today', 0)} new, "
        f"{stats.get('qualified', 0)} qualified, "
        f"{stats.get('with_email', 0)} with email"
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
