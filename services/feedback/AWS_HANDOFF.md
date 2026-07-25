# Sentinel Feedback AWS Deployment Prompt

You are working on the Fusion repository for the Sentinel community safety platform.

Repository:

```text
https://github.com/raeescassoojee/fusion
```

The customer feedback and LDA work is on this branch:

```text
cassoojee
```

## First actions

Pull the latest branch:

```bash
git fetch origin
git switch cassoojee
git pull origin cassoojee
```

Before changing anything, inspect:

```text
services/feedback/
services/chat/
services/claims/data/curated/hotspots.json
.gitignore
```

Pay particular attention to:

```text
services/feedback/client.html
services/feedback/server.js
services/feedback/package.json
services/feedback/package-lock.json
services/feedback/data/mock_feedback.csv
services/feedback/data/processed_feedback.csv
services/feedback/ml/preprocess.py
services/feedback/ml/train_lda.py
services/feedback/ml/requirements.txt
services/feedback/ml/outputs/topics.json
services/feedback/ml/outputs/document_topics.csv
services/feedback/ml/outputs/model_comparison.csv
services/feedback/ml/outputs/lda_model.joblib
services/feedback/ml/outputs/vectorizer.joblib
```

Do not replace working functionality without first understanding it. Preserve the current frontend design, API response shapes, all 14 Sentinel hotspot locations, the existing chat service, and the current LDA outputs.

## Existing functionality

The feedback service currently provides:

```text
GET  /
GET  /health
GET  /api/locations
GET  /api/categories
GET  /api/topics
GET  /api/feedback/stats
POST /api/feedback
```

The feedback interface includes:

* All 14 Sentinel locations
* Ratings from 1 to 5
* Feedback categories
* A feedback text field
* Client-side validation
* Server-side validation
* Live feedback statistics
* Responsive Sentinel styling

The local feedback service uses:

```text
PORT=8090
HOST=0.0.0.0
```

The current local feedback file is:

```text
services/feedback/data/live_feedback.jsonl
```

This local JSONL file is only for development. Do not use it as the production AWS database.

The existing LDA pipeline includes:

* 280 balanced mock feedback records
* 14 locations
* Seven known mock themes
* Text preprocessing
* LDA topic-count comparison
* A saved scikit-learn LDA model
* A saved CountVectorizer
* Document topic assignments
* Topic results in JSON format

## Main objective

Deploy the feedback service on the existing AWS infrastructure with minimal disruption.

The production flow should be:

```text
Feedback UI
    |
    v
Node feedback API
    |
    v
DynamoDB
    |
    v
Scheduled Python topic pipeline
    |
    v
S3 model and topic outputs
    |
    v
GET /api/topics
    |
    v
UI topic dashboard
```

Reuse existing AWS resources, infrastructure-as-code, Docker configuration, networking, domains, certificates, IAM roles, CI/CD workflows, and deployment conventions wherever possible.

Do not create duplicate infrastructure when an appropriate resource already exists.

## Requirement 1: Preserve local development

The application must continue working locally.

Use an environment variable:

```text
FEEDBACK_STORE=local
```

Local mode should store new submissions in:

```text
services/feedback/data/live_feedback.jsonl
```

Local startup should remain simple:

```bash
cd services/feedback
npm install
npm start
```

The local URL should remain:

```text
http://localhost:8090
```

## Requirement 2: Add DynamoDB production storage

AWS production mode should use:

```text
FEEDBACK_STORE=dynamodb
```

Use AWS SDK for JavaScript v3.

Do not hardcode AWS credentials.

Use the ECS task IAM role or the existing AWS workload role.

Expected environment variables:

```text
NODE_ENV=production
PORT=8090
HOST=0.0.0.0
FEEDBACK_STORE=dynamodb
AWS_REGION=af-south-1
FEEDBACK_TABLE=sentinel-feedback
MODEL_BUCKET=sentinel-feedback-ml
TOPICS_KEY=results/topics.json
```

Reuse the existing AWS Region and existing resource names if they differ.

The DynamoDB feedback records must preserve this schema:

```json
{
  "feedback_id": "REAL-generated-uuid",
  "submitted_at": "ISO-8601 timestamp",
  "user_id": "anonymous or authenticated user ID",
  "hotspot_id": "H001",
  "location": "Rondebosch",
  "metro": "Cape Town",
  "rating": 4,
  "category": "Safety Alerts",
  "feedback_text": "Feedback supplied by the user",
  "platform": "Web",
  "status": "new",
  "source": "real",
  "topic_id": null,
  "topic_label": null,
  "topic_confidence": null,
  "model_version": null
}
```

Use `feedback_id` as the primary key unless the existing table design requires a compatible alternative.

Useful indexes may include:

```text
hotspot_id + submitted_at
category + submitted_at
status + submitted_at
```

Avoid table scans in user-facing API requests if the table is expected to grow.

## Requirement 3: Create a storage abstraction

The Node server should not contain separate duplicated API implementations for local and AWS modes.

Use a storage interface similar to:

```javascript
await feedbackStore.save(record);
await feedbackStore.getStats();
await feedbackStore.listForAnalysis();
```

Recommended structure:

```text
services/feedback/storage/
├── index.js
├── local-store.js
└── dynamodb-store.js
```

`storage/index.js` should select the implementation using `FEEDBACK_STORE`.

Invalid production configuration should fail during startup with a clear error.

## Requirement 4: Connect new feedback to the LDA pipeline

New real feedback must enter future LDA processing automatically.

Do not retrain the LDA model inside `POST /api/feedback`.

The web request should only:

1. Validate the feedback.
2. Save the feedback.
3. Return a successful response.

The Python pipeline should separately:

1. Load mock feedback when explicitly enabled.
2. Load real feedback from JSONL in local mode.
3. Load real feedback from DynamoDB in AWS mode.
4. Validate the shared schema.
5. Clean and preprocess new feedback.
6. Use the current vectorizer and model to classify new feedback.
7. Assign a topic ID, label, confidence, and model version.
8. Write classification results back to DynamoDB.
9. Retrain LDA only according to a schedule or threshold.
10. Publish updated topic summaries and model artifacts.

Recommended behaviour:

```text
Classification: frequent scheduled batch
Retraining: daily, weekly, or after enough new feedback
```

Use the existing `preprocess.py` and `train_lda.py` as the starting point. Do not remove the reproducible random state or model comparison outputs.

## Requirement 5: Publish topic results to S3

Store generated outputs using a structure similar to:

```text
s3://MODEL_BUCKET/
├── models/
│   ├── lda_model.joblib
│   └── vectorizer.joblib
├── results/
│   ├── topics.json
│   ├── document_topics.csv
│   └── model_comparison.csv
└── exports/
    └── feedback_export.csv
```

Use S3 versioning if it is already enabled or straightforward to enable.

The topic JSON should contain:

```json
{
  "model_version": "timestamp or version identifier",
  "generated_at": "ISO-8601 timestamp",
  "selected_topic_count": 7,
  "documents_analysed": 280,
  "real_documents_analysed": 0,
  "topics": [
    {
      "topic_id": 1,
      "topic_key": "topic_1",
      "label": "Safety Alerts",
      "document_count": 40,
      "percentage": 14.29,
      "category_purity": 0.95,
      "top_words": [
        "alert",
        "warning",
        "notification",
        "delayed",
        "urgent"
      ]
    }
  ]
}
```

Keep the existing `likely_category` field if changing it would break the current UI. Supporting both `label` and `likely_category` is acceptable.

## Requirement 6: Serve topics through the API

The UI must use:

```text
GET /api/topics
```

Do not make the browser access private S3 objects directly.

In local mode, `/api/topics` may read:

```text
services/feedback/ml/outputs/topics.json
```

In AWS mode, it should read:

```text
s3://MODEL_BUCKET/TOPICS_KEY
```

Cache the S3 topic response briefly to avoid fetching the same object for every page request.

Return a useful `404` response if no model has been published yet.

## Requirement 7: Integrate topic results into the UI

Add a clear topic-summary area to the existing feedback UI or the appropriate existing Sentinel dashboard.

Display:

* Topic label
* Percentage of analysed feedback
* Number of documents
* Top keywords
* Model generation time
* Number of real feedback records analysed

Do not expose:

* Raw user feedback
* User IDs
* Personal information
* Private DynamoDB attributes
* Internal S3 paths
* AWS credentials

The public feedback form must remain simple and should not be blocked if topic results are temporarily unavailable.

Use safe DOM operations such as `textContent` for topic content returned by the API.

## Requirement 8: Schedule the ML pipeline

Use the existing AWS scheduling approach if one is already present.

Otherwise, use an EventBridge scheduled ECS task or another appropriate existing project pattern.

The scheduled pipeline should:

1. Start independently of the web service.
2. Read feedback from DynamoDB.
3. Classify unprocessed records.
4. Retrain only when required.
5. Upload results to S3.
6. Log success and failure.
7. Exit with a nonzero status when processing fails.

Do not run an always-active Python training process inside the Node web container unless the existing architecture explicitly requires it.

## Requirement 9: Security and operations

Ensure:

* No AWS keys are committed
* `.env` remains ignored
* Real feedback files remain ignored
* Request bodies have size limits
* Feedback length is validated
* Ratings and categories are validated
* Hotspot IDs are validated against the curated hotspot data
* Production HTTPS is used
* `/health` works through the load balancer
* ECS shutdown signals are handled cleanly
* Logs go to the existing logging system or CloudWatch
* IAM permissions follow least privilege
* CORS is restricted if the frontend and API use different origins
* Rate limiting is added to `POST /api/feedback`
* Raw stack traces are not returned to users

## Requirement 10: Deployment compatibility

Reuse the existing Docker and AWS deployment work.

Ensure the production image contains the files required at runtime:

```text
client.html
server.js
package.json
package-lock.json
storage/
ml/requirements.txt
ml/preprocess.py
ml/train_lda.py
ml/outputs/topics.json
services/claims/data/curated/hotspots.json
```

If the feedback container cannot access `services/claims/data/curated/hotspots.json`, either:

* Copy the curated hotspot file into the image, or
* Provide its location through `HOTSPOTS_FILE`.

Do not hardcode a Windows path.

## Required testing

Before deployment, run:

```bash
cd services/feedback
npm ci
npm start
```

Verify:

```text
GET /health
GET /api/locations
GET /api/categories
GET /api/topics
GET /api/feedback/stats
POST /api/feedback
```

Run the Python checks:

```bash
python ml/preprocess.py
python ml/train_lda.py
```

Verify that:

* All 14 locations load
* Valid feedback is stored
* Invalid feedback is rejected
* Local mode still works
* DynamoDB mode writes successfully
* Topic JSON remains valid
* The saved model and vectorizer load
* New feedback can receive a topic assignment
* The UI still submits feedback
* The UI can display topic summaries
* No secrets or real feedback are committed

## Git safety

Work on a separate deployment branch unless instructed otherwise.

Do not force-push.

Do not overwrite unrelated work in:

```text
services/chat/
services/claims/
```

Preserve existing user changes and inspect `git status` before committing.

## Required handoff response

After completing the task, report:

1. Files changed
2. AWS resources reused
3. AWS resources created
4. Environment variables required
5. IAM permissions required
6. Local test results
7. AWS test results
8. Deployed URL
9. Remaining limitations
10. How to trigger classification and retraining manually

Do not claim deployment success until the deployed `/health`, feedback submission, DynamoDB record, `/api/topics`, and UI topic display have all been verified.
