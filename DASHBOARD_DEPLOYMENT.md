# Deploy Dashboard to Azure Static Web Apps

## Quick Deploy (5 mins)

### 1. Create Static Web App (Azure Portal)
- Go to Azure Portal → Create Resource → Static Web App
- **Name:** trbot-dashboard
- **Resource Group:** rg-trbot-prod
- **Hosting Plan:** Free tier
- **Repository:** Skip (we'll deploy manually)
- **Region:** East US (or your region)

### 2. Manual Deployment via Azure CLI

```powershell
# Install Static Web Apps CLI
npm install -g @azure/static-web-apps-cli

# From the web folder
cd web

# Build (just copy the files)
npm run build  # if using npm scripts

# Deploy
swa deploy --deployment-token YOUR_DEPLOYMENT_TOKEN
```

**Get deployment token from:** Azure Portal → Your Static Web App → Deployment tokens

### 3. Via GitHub Actions (Recommended)
1. Push the `web/` folder to GitHub
2. Static Web Apps auto-detects and creates a GitHub Actions workflow
3. Every push to main → auto-deploys!

## Local Test
```powershell
cd web
python -m http.server 8000
# Open http://localhost:8000
# Click Settings, paste your API key
```

## What This Dashboard Does
- ✅ Search any ticker (BTC-USD, NVDA, ETH-USD, etc.)
- ✅ Shows 3 trading styles: Scalp / Swing / Long-Term
- ✅ Entry levels, stops, targets, R:R ratios
- ✅ Supporting signals from multi-timeframe analysis
- ✅ Mobile responsive (dark theme)
- ✅ No backend — all calls to your Azure Functions

## Files
- `index.html` — Complete dashboard (all-in-one)
- `staticwebapp.config.json` — SWA routing config
- `package.json` — Can add build scripts if needed

---

## Cost
- **Static Web App Free Tier:** 100% free for bare domains
- **Azure Functions:** Already deployed (existing cost)
- **Total additional cost:** $0

✨ Your new dashboard is ready to deploy!
