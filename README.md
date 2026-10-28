# Market dashboard website: setup guide

This turns the dashboard into a website with a link you can share, that updates itself every day and keeps its history.
Anyone with the link can open it. Total effort: about 15 minutes of clicking. I could not test the GitHub steps from here, so the first run may need a fix (see the end).

## What you get
- A web address like `https://YOUR-NAME.github.io/market-monitor/`
- A daily automatic refresh at about 06:30 UTC
- A history that only grows: each day's new numbers are added to the data files, nothing is deleted, and GitHub also keeps every day's version

## Set-up checklist

1. **Make a GitHub account** at github.com (Sign up). Free plan is fine.
2. **Create a repository:** click **+** (top right) then **New repository**. Name it `market-monitor`. Choose **Public**. Click **Create repository**.
3. **Upload the files:** unzip this package. On the new repository page click **uploading an existing file**, then drag in everything from the unzipped folder: `site`, `refresh_data.py`, `README.md` and `workflow-copy-me.yml`. Click **Commit changes**.
4. **Add the daily job:** in the repository click **Add file**, then **Create new file**. In the name box type exactly `.github/workflows/update-and-deploy.yml` (typing the slashes creates the folders). Paste in the contents of `workflow-copy-me.yml`. Click **Commit changes**.
5. **Turn on the website:** **Settings**, then **Pages**. Under **Build and deployment**, set **Source** to **GitHub Actions**.
6. **Allow the job to save data:** **Settings**, then **Actions**, then **General**. Under **Workflow permissions** choose **Read and write permissions**, then **Save**.
7. **Run it the first time:** click the **Actions** tab, choose **Update data and deploy** on the left, click **Run workflow**, then the green **Run workflow** button. Wait a few minutes.
8. **Get your link:** when the run shows a green tick, open **Settings**, then **Pages**. The address is at the top. Send it to your friends.

## What updates by itself, and what does not
**Automatic, every day:** WTI, Brent, gas, the 10-year yield, gold, S&P 500, VIX, exchange rates, inflation, CO2 and temperature (from EIA, FRED and public datasets), and silver, platinum and palladium (the metals feed keeps only 30 days, so their history builds up from your first run).

**By hand (a script cannot read news sites):** the news-site prices, the headline oil and S&P tiles, Claude's conclusion, the reading guides, auctions, the yield curve, conflicts, solar and wind, and nuclear. These stay as they are until you replace them. After you say "refresh my dashboard" to me, I can give you updated files: in the repository open `site/data`, click **Add file**, **Upload files**, drag the new files in, and commit. The site then redeploys by itself. The freshness table on the site turns these rows red as they age, so nobody is misled.

## Things to know
- **Public:** the website, the code and every data file are visible to anyone. I checked the package: it holds only public-source data and my analysis, nothing about you. A link is not a password, so do not post it where you would not want strangers to find it. The page asks search engines not to list it, but that is a request, not protection.
- **Data licences:** I have not checked the licence terms of every source. For a private circle that is low risk; check before posting it widely.
- **Cost:** GitHub's documentation says Pages is free from public repositories. I did not check Actions usage limits; one small daily job uses very little.
- **Schedule switch-off:** GitHub's documentation says that in a public repository, scheduled jobs are switched off after 60 days with no activity. The job saves data daily and re-enables itself each run, which should prevent that, but I could not test it.

## What I tested, and what I could not
**Tested here:** the refresh script's history logic (running it over the real files lost no values, and removed rows were added back); the website in a simulated browser loading its data files (no errors, all charts and tables drawn).
**Not tested:** the GitHub workflow itself; whether FRED and the metals feed answer when called from GitHub's servers; and the exact versions of the GitHub actions it uses. If the first run shows a red cross, open the failed run, copy the red error text and send it to me. I will fix the file.

## The workflow file (also saved as workflow-copy-me.yml)
```yaml
name: Update data and deploy
on:
  schedule:
    - cron: "30 6 * * *"        # every day at 06:30 UTC
  workflow_dispatch: {}          # lets you run it by hand from the Actions tab
  push:
    branches: [main]
    paths: ["site/**"]           # runs when you upload new files to the site folder
permissions:
  contents: write
  pages: write
  id-token: write
  actions: write
concurrency:
  group: pages
  cancel-in-progress: false
jobs:
  update:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      - uses: actions/setup-python@v5
        with:
          python-version: "3.12"
      - name: Refresh data (never deletes history)
        run: python refresh_data.py site/data
      - name: Save the new data in the repository
        run: |
          git config user.name "refresh-bot"
          git config user.email "refresh-bot@users.noreply.github.com"
          git add site/data
          if git diff --cached --quiet; then echo "No new data today"; else git commit -m "Data update $(date -u +%F)"; git push; fi
      - name: Keep the daily schedule switched on
        run: curl -s -X PUT -H "Authorization: Bearer ${{ secrets.GITHUB_TOKEN }}" -H "Accept: application/vnd.github+json" "https://api.github.com/repos/${{ github.repository }}/actions/workflows/update-and-deploy.yml/enable" || true
      - uses: actions/configure-pages@v5
      - uses: actions/upload-pages-artifact@v3
        with:
          path: site
  deploy:
    needs: update
    runs-on: ubuntu-latest
    environment:
      name: github-pages
      url: ${{ steps.deployment.outputs.page_url }}
    steps:
      - id: deployment
        uses: actions/deploy-pages@v4
```
