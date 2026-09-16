# Privacy Policy for SyncbitHealth

_Last updated: 2026-09-16_

SyncbitHealth ("SyncBit") is open-source, self-hosted software that copies a
person's own health and fitness data from the Google Health API into a time
series database that they themselves operate.

It is not a hosted service. There is no SyncBit server, no SyncBit account, and
no SyncBit operator with access to anyone's data. Each person runs their own
copy on their own infrastructure, using their own Google Cloud OAuth client.

## Who this policy covers

This policy describes the behaviour of the software published at
https://github.com/origox/syncbit. Where it says "you", it means the person who
installs and runs that software on their own machine or cluster, using their own
Google credentials to access their own health data.

## What data is accessed

When you authorize SyncBit, it requests read-only access to these Google Health
API scopes:

- `googlehealth.activity_and_fitness.readonly` — steps, distance, calories,
  active minutes, heart rate zones
- `googlehealth.health_metrics_and_measurements.readonly` — heart rate, resting
  heart rate, heart rate variability, blood oxygen saturation, VO2 max,
  respiratory rate, skin temperature
- `googlehealth.sleep.readonly` — sleep duration and sleep stages
- `googlehealth.profile.readonly` — account identifier and account creation
  date, used to determine how far back historical data can be requested

SyncBit requests no write scopes and cannot create, modify or delete any data in
your Google account.

## How data is used

Data is read from the Google Health API and written, unmodified in substance, to
a Prometheus-compatible time series database (Victoria Metrics) that you
configure and control. The purpose is to let you chart and analyse your own
health data with your own tools, such as Grafana.

Data is not used for advertising, profiling, training machine learning models,
or any purpose beyond storing it in the database you nominate.

## Where data is stored

- **Health metrics** are written only to the Victoria Metrics endpoint you
  configure. That endpoint is yours; this project neither provides nor operates
  one.
- **OAuth tokens** (access and refresh) are stored on the machine running
  SyncBit, in a local file with owner-only permissions (`0600`), or in a
  Kubernetes secret when deployed to a cluster.
- **Sync progress** (the last successfully synced date) is stored in a local
  JSON file so that interrupted backfills can resume.

Nothing is transmitted to the author of this software, and nothing is
transmitted to any third party. The only network destinations SyncBit contacts
are Google's OAuth and Health API endpoints, and the database endpoint you
configure.

## Data sharing

None. This software has no analytics, no telemetry, no crash reporting, and no
outbound connections other than those described above.

## Data retention and deletion

You control retention entirely, because you control the storage.

- To delete stored health metrics, delete them from your own database.
- To delete stored credentials, delete the token file (by default
  `data/google_tokens.json`) or the corresponding Kubernetes secret.
- To revoke SyncBit's access to your Google account at any time, visit
  https://myaccount.google.com/permissions and remove the application. Revoking
  access immediately prevents any further reads; data already written to your
  own database is unaffected and remains under your control.

## Children

This software is not directed at children and is not intended for use by anyone
under the age of 13.

## Security

OAuth tokens are stored with owner-only file permissions. All communication with
Google's APIs and with your database endpoint uses HTTPS, subject to your own
endpoint configuration. Because SyncBit runs on infrastructure you control,
securing that infrastructure is your responsibility.

## Changes to this policy

Changes will be committed to this file in the public repository, and the "last
updated" date above will be revised. The revision history is visible in the
repository's Git history.

## Contact

Questions about this policy can be raised as an issue at
https://github.com/origox/syncbit/issues.
