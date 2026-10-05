import os
from dotenv import load_dotenv

load_dotenv()

ANTHROPIC_API_KEY = os.environ["ANTHROPIC_API_KEY"]
HUBSPOT_API_TOKEN = os.environ["HUBSPOT_API_TOKEN"]
HUBSPOT_API_TOKEN_ANALISE = os.environ.get("HUBSPOT_API_TOKEN_ANALISE", os.environ["HUBSPOT_API_TOKEN"])
SLACK_BOT_TOKEN = os.environ["SLACK_BOT_TOKEN"]
SLACK_CHANNEL_ID = os.environ["SLACK_CHANNEL_ID"]
SLACK_CHANNEL_ANALISE_ID = os.environ.get("SLACK_CHANNEL_ANALISE_ID", os.environ["SLACK_CHANNEL_ID"])

HUBSPOT_BASE_URL = "https://api.hubapi.com"
SLACK_BASE_URL = "https://slack.com/api"
