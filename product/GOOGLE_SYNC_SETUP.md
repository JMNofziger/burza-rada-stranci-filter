# Turn on "Save my list to Google"

This lets people who use the board keep their **Interested** and **Applied** marks on every device by signing in with Google. You set it up once. It takes about 15 minutes, costs nothing, and needs no coding.

What you need: a Google account, and access to this repository's **Settings** on GitHub.

What it does: each person's marks are saved in a hidden folder in **their own** Google Drive that only this board can read. The board never sees their email, files, or anything else. Nothing is stored on a server.

Until you finish step 8, the board works exactly as before and the Google button is hidden.

---

## 1. Create a project

1. Go to [console.cloud.google.com](https://console.cloud.google.com) and sign in.
2. Click the project picker at the top of the page (next to "Google Cloud"), then **New project**.
3. Project name:

   ```
   zagreb-jobs-board
   ```

4. Click **Create**. When it finishes, open the project picker again and make sure `zagreb-jobs-board` is selected.

## 2. Turn on Google Drive

1. In the search bar at the top, type `Google Drive API` and open it.
2. Click **Enable**.

## 3. Describe the app

1. Open the menu (three lines, top left) > **Google Auth Platform**. Click **Get started**.
2. **App name:**

   ```
   Zagreb jobs board
   ```

3. **User support email:** pick your email. Click **Next**.
4. **Audience:** choose **External**. Click **Next**.
5. **Contact information:** type your email. Click **Next**.
6. Tick the box to agree to the policy, then **Continue** and **Create**.

Do **not** upload a logo anywhere. A logo makes Google review the app, which takes several days.

## 4. Allow the one permission

1. In the left menu of Google Auth Platform, click **Data access**.
2. Click **Add or remove scopes**.
3. In the filter box, type:

   ```
   drive.appdata
   ```

4. Tick the row ending in `.../auth/drive.appdata`. Click **Update**, then **Save** at the bottom of the page.

## 5. Open it to everyone

1. Left menu: **Audience**.
2. Under "Publishing status", click **Publish app**, then **Confirm**.

If you skip this, only people you list by hand can sign in, and they have to sign in again every 7 days.

## 6. Create the website key

1. Left menu: **Clients** > **Create client**.
2. **Application type:** Web application.
3. **Name:**

   ```
   Pages board
   ```

4. Under **Authorized JavaScript origins**, click **Add URI** and paste exactly this (no path, no slash at the end):

   ```
   https://jmnofziger.github.io
   ```

5. Leave **Authorized redirect URIs** empty. Click **Create**.
6. Copy the **Client ID**. It ends in `.apps.googleusercontent.com`. It is not a secret; it is fine to paste it into GitHub.

## 7. Give it to the website

1. Open [the repository settings](https://github.com/JMNofziger/burza-rada-stranci-filter/settings/variables/actions): **Settings** > **Secrets and variables** > **Actions**.
2. Click the **Variables** tab (not Secrets).
3. Click **New repository variable**.
4. **Name:**

   ```
   GOOGLE_CLIENT_ID
   ```

5. **Value:** paste the Client ID from step 6. Click **Add variable**.

## 8. Publish

1. Open the repository's **Actions** tab.
2. In the left list, click **Deploy jobs board**.
3. Click **Run workflow**, then the green **Run workflow** button.
4. Wait about 2 minutes, until the run shows a green check.

## 9. Check it

1. Open [the board](https://jmnofziger.github.io/burza-rada-stranci-filter/) (refresh if it was already open).
2. Open **Filters** and find **My jobs**. You should see **Save my list to Google**.
3. Click it, then **Continue to Google**, and sign in. You should see "Saved to Google" with the time.

Google can take up to 5 minutes to apply new settings. If the first try fails, wait a few minutes and try again.

---

## If something goes wrong

| What you see | What to do |
|--------------|------------|
| "Error 400: origin_mismatch" | In step 6, the address has a typo. It must be exactly `https://jmnofziger.github.io`, with `https`, and no slash at the end. |
| "Access blocked: this app has not completed verification" or "not a test user" | Step 5 was not finished. Publish the app. |
| "Google didn't allow saving" on the board | During sign-in, the box that lets the site save its list was unticked. Click the button again and leave it ticked. |
| No "Save my list to Google" button | In step 7 the value went into **Secrets** instead of **Variables**, or the name is not exactly `GOOGLE_CLIENT_ID`, or step 8 was not run afterwards. |
| "Open this page in Safari or Chrome" | Expected inside Telegram and other apps' built-in browsers. Google blocks sign-in there. Tap **Copy link** and open it in a normal browser. |

To turn the feature off, delete the `GOOGLE_CLIENT_ID` variable and run step 8 again. People keep the marks already on their devices.

---

## Developer appendix: local testing

1. In step 6, also add this origin to the same client:

   ```
   http://localhost:8000
   ```

2. Write the config locally and serve the board:

   ```bash
   GOOGLE_CLIENT_ID=your-id.apps.googleusercontent.com python3 -m web.sync_config
   python3 -m http.server 8000 --directory docs
   ```

3. Open `http://localhost:8000`. Do not commit `docs/sync-config.js` with a real ID (`git checkout docs/sync-config.js` resets it).

Technical notes: Google Identity Services token client, scope `https://www.googleapis.com/auth/drive.appdata`, one file `hzz-job-tracker.json` in the user's Drive `appDataFolder`. The access token lives in memory only. Users can remove the hidden data in Google Drive > Settings > Manage apps.
