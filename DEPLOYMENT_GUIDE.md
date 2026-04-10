# Hugging Face Space Deployment Guide

## 📋 Pre-Deployment Checklist

| Item | Status | Notes |
|------|--------|-------|
| ✅ inference.py exists | Ready | In root directory |
| ✅ openenv.yaml exists | Ready | In root directory |
| ✅ Dockerfile exists | Ready | 11 lines, Python 3.12-slim |
| ✅ 3 tasks with graders | Ready | easy/medium/hard |
| ✅ All tests pass | Ready | 40/40 tests |
| ✅ Runtime secrets documented | Ready | API_BASE_URL, MODEL_NAME, HF_TOKEN, APP_API_BASE_URL |

---

## 🔧 Deployment Specs

### Docker Configuration
```dockerfile
FROM python:3.12-slim
WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY . .
EXPOSE 8000
CMD ["uvicorn", "env.api:app", "--host", "0.0.0.0", "--port", "8000"]
```

### Resource Requirements
- **Memory**: 8GB RAM (minimum)
- **CPU**: 2 vCPU
- **Timeout**: Inference must complete in <20 minutes
- **Port**: 8000 (exposed)

### Environment Variables (Required for HF Space)
| Variable | Description | Example |
|----------|-------------|---------|
| `APP_API_BASE_URL` | Dashboard -> FastAPI URL | `http://127.0.0.1:8000` |
| `API_BASE_URL` | LLM provider endpoint | `https://api.openai.com/v1` |
| `MODEL_NAME` | Model ID | `gpt-4o-mini` |
| `HF_TOKEN` | API key | `hf_...` |
| `AZURE_API_VERSION` (optional) | Azure API version override | `2024-02-15-preview` |

---

## 🚀 Deployment Steps

### Step 1: Prepare Your Repository

```bash
# Ensure these files exist in root:
ls -la inference.py openenv.yaml Dockerfile README.md requirements.txt
```

### Step 2: Create Hugging Face Space

1. Go to https://huggingface.co/spaces
2. Click **"Create new Space"**
3. Fill in:
   - **Owner**: Your username
   - **Space name**: `email-copilot-env` (or your choice)
   - **License**: MIT
   - **Visibility**: Public (or Private)
   - **SDK**: Streamlit
   - **Hardware**: CPU (2 vCPU, 8GB RAM)

### Step 3: Add Secrets (Environment Variables)

In your HF Space settings, add these secrets:

| Secret | Value |
|--------|-------|
| `APP_API_BASE_URL` | `http://127.0.0.1:8000` |
| `API_BASE_URL` | `https://api.openai.com/v1` (or Azure/OpenAI endpoint) |
| `MODEL_NAME` | `gpt-4o-mini` |
| `HF_TOKEN` | Your OpenAI/HF API key |

**Important**: The inference script expects these exact variable names.

### Step 4: Push to Hugging Face

```bash
# Option A: Using git directly
git add .
git commit -m "Add OpenEnv submission files"
git push

# Option B: Using Hugging Face CLI
huggingface-cli upload-space email-copilot-env . --token HF_TOKEN
```

### Step 5: Wait for Deployment

- HF will build the Docker container
- This takes 2-5 minutes typically
- Check the "Build logs" tab for progress

### Step 6: Verify Deployment

Test your Space:

1. Open the Space URL and confirm the Overview tab shows API online.
2. In the sidebar, confirm API Base URL is `http://127.0.0.1:8000`.
3. Click **Check API Health**.
4. Open docs at `http://127.0.0.1:8000/docs`.

---

## 📝 inference.py Requirements

Your inference.py must:
1. Be in **root** directory (not subfolder)
2. Use environment variables: `API_BASE_URL`, `MODEL_NAME`, `HF_TOKEN`
3. Output EXACT logging format:
   ```
   [START] task=X env=Y model=Z
   [STEP] step=N action=A reward=R done=D error=E
   [END] success=S steps=T score=Sc rewards=Rl
   ```

---

## 🔍 Troubleshooting

### Build Fails
- Check Dockerfile syntax
- Ensure requirements.txt is valid
- Check build logs in HF Space

### /health Returns 404 or Connection Refused
- Verify `APP_API_BASE_URL` is set to `http://127.0.0.1:8000`
- Do not set dashboard API URL to Azure/OpenAI provider URL
- Check Space runtime logs for FastAPI auto-start failures

### API Errors
- Verify HF_TOKEN is set in Space secrets
- Check API_BASE_URL is correct
- Ensure MODEL_NAME is valid

---

## 📦 Expected File Structure

```
email-copilot-env/
├── inference.py        # MUST be in root
├── openenv.yaml        # MUST be in root
├── Dockerfile          # MUST be in root
├── README.md
├── requirements.txt
├── env/
│   ├── api.py
│   ├── environment.py
│   ├── models.py
│   ├── llm_agent.py
│   ├── grader.py
│   └── ...
└── data/
    ├── tasks.yaml
    └── scenarios/
```

---

## ✅ Final Validation Commands

Before submitting, run locally:

```bash
# 1. Test inference.py (requires API key)
export API_BASE_URL="https://api.openai.com/v1"
export MODEL_NAME="gpt-4o-mini"
export HF_TOKEN="sk-..."
python inference.py

# 2. Test API locally
uvicorn env.api:app --host 0.0.0.0 --port 8000

# 3. Test health and tasks
curl http://localhost:8000/health
curl http://localhost:8000/tasks

# 4. Run all tests
python -m pytest -q
```

All must pass for submission to be valid.