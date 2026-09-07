# Privacy Policy — Countries Shorts Pipeline

_Last updated: 2026-09-07_

Countries Shorts Pipeline is a personal, single-owner automation project that generates
and publishes short-form videos about world countries. It is not a public-facing app or
service, and it does not have any end users other than its own developer/operator.

## What this application does

The application runs on a schedule (via GitHub Actions) and uses the YouTube Data API
solely to upload videos, produced by this pipeline, to the operator's own YouTube
channel. It requests the `youtube.upload` OAuth scope for this single purpose.

## What data is accessed

The only data accessed via Google APIs is the operator's own YouTube channel, for the
purpose of uploading videos the operator created. No data belonging to any other user
is accessed, collected, stored, or shared.

## What data is stored

No personal data is collected from or about third parties. OAuth credentials
(client ID, client secret, refresh token) are stored as encrypted secrets in the
operator's own private GitHub repository configuration and are used only to
authenticate the operator's own uploads.

## Data sharing

No data is sold, shared, or disclosed to any third party.

## Contact

Questions about this project can be sent to: mmw123450@gmail.com
