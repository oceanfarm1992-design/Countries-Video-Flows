/**
 * Google Apps Script "Web App" that appends one row to THIS sheet when it receives a
 * POST from scripts/post_sheet.py. This is the no-Google-Cloud way to write to a sheet:
 * no service account, no JSON key — just a normal Google account.
 *
 * SETUP (one time):
 *   1. Open your Google Sheet. Make sure row 1 has these headers, in this order:
 *          title | description | hashtags | caption | video_url | category
 *   2. Extensions -> Apps Script. Delete any sample code, paste THIS file, and Save.
 *   3. (Optional but recommended) set SHEET_TOKEN below to a random string, and put the
 *      SAME string in the GitHub secret SHEET_WEBHOOK_TOKEN so only your pipeline can write.
 *   4. Deploy -> New deployment -> type "Web app".
 *        - Execute as:  Me
 *        - Who has access:  Anyone
 *      Click Deploy, authorize when prompted, and COPY the Web app URL (ends in /exec).
 *   5. Put that URL in the GitHub secret SHEET_WEBHOOK_URL.
 *
 * To change the code later you must Deploy -> Manage deployments -> edit -> new version
 * (or the old URL keeps running the old code).
 */

// Optional shared secret. Leave "" to disable the check. If set, it must equal the
// GitHub secret SHEET_WEBHOOK_TOKEN.
var SHEET_TOKEN = "";

// Which tab to append to. "" = the first sheet in the spreadsheet.
var SHEET_NAME = "";

function doPost(e) {
  try {
    var body = JSON.parse(e.postData.contents);

    if (SHEET_TOKEN && body.token !== SHEET_TOKEN) {
      return _json({ ok: false, error: "forbidden" });
    }

    var ss = SpreadsheetApp.getActiveSpreadsheet();
    var sheet = SHEET_NAME ? ss.getSheetByName(SHEET_NAME) : ss.getSheets()[0];

    sheet.appendRow([
      body.title || "",
      body.description || "",
      body.hashtags || "",
      body.caption || "",
      body.video_url || "",
      body.category || ""
    ]);

    return _json({ ok: true });
  } catch (err) {
    return _json({ ok: false, error: String(err) });
  }
}

// Lets you sanity-check the deployment in a browser (should print {"ok":true,...}).
function doGet() {
  return _json({ ok: true, status: "alive" });
}

function _json(obj) {
  return ContentService
    .createTextOutput(JSON.stringify(obj))
    .setMimeType(ContentService.MimeType.JSON);
}
