"""Central model configuration for the PR code review agent."""

BEDROCK_PROXY_URL = "https://ug36pewdpyfaepokw55klfit7y0ltgbn.lambda-url.us-west-2.on.aws/"

# Model IDs
TRIAGE_MODEL = "us.meta.llama3-3-70b-instruct-v1:0"
REVIEW_MODEL = "qwen.qwen3-coder-480b-a35b-v1:0"
REVIEW_MODEL_FALLBACK = "us.meta.llama4-maverick-17b-instruct-v1:0"
SECURITY_MODEL = "us.deepseek.r1-v1:0"

# Token limits
TRIAGE_MAX_TOKENS = 1024
REVIEW_MAX_TOKENS = 4096
SECURITY_MAX_TOKENS = 4096

# Review thresholds
CONFIDENCE_THRESHOLD = 0.75
LARGE_FILE_LINE_THRESHOLD = 300
CHUNK_TOKEN_SIZE = 3000
PR_TOKEN_BUDGET = 80000

# Retry config
MAX_RETRIES = 3
BACKOFF_SECONDS = 2
