"use strict";

const crypto = require("crypto");
const fs = require("fs");
const path = require("path");
const express = require("express");


const app = express();

const PORT = Number(
  process.env.PORT || 8090
);

const HOST =
  process.env.HOST || "0.0.0.0";

const ROOT_DIR = __dirname;

const CLIENT_FILE = path.join(
  ROOT_DIR,
  "client.html"
);

const DATA_DIR = path.join(
  ROOT_DIR,
  "data"
);

const LIVE_FEEDBACK_FILE = path.join(
  DATA_DIR,
  "live_feedback.jsonl"
);

const TOPICS_FILE = path.join(
  ROOT_DIR,
  "ml",
  "outputs",
  "topics.json"
);

const HOTSPOTS_FILE =
  process.env.HOTSPOTS_FILE ||
  path.join(
    ROOT_DIR,
    "..",
    "claims",
    "data",
    "curated",
    "hotspots.json"
  );

const MIN_FEEDBACK_LENGTH = 10;
const MAX_FEEDBACK_LENGTH = 1000;

const VALID_CATEGORIES = [
  "Safety Alerts",
  "Location Accuracy",
  "Incident Reporting",
  "Community Chat",
  "Application Performance",
  "Privacy and Trust",
  "Positive Experience",
  "Other"
];


app.disable("x-powered-by");

app.use(
  express.json({
    limit: "20kb"
  })
);


function cleanString(
  value,
  maximumLength
) {
  return String(value ?? "")
    .replace(
      /[\u0000-\u001f\u007f]/g,
      " "
    )
    .replace(/\s+/g, " ")
    .trim()
    .slice(0, maximumLength);
}


function loadHotspots() {
  try {
    const contents = fs.readFileSync(
      HOTSPOTS_FILE,
      "utf8"
    );

    const parsed = JSON.parse(contents);

    let records = [];

    if (Array.isArray(parsed)) {
      records = parsed;
    } else if (
      Array.isArray(parsed.hotspots)
    ) {
      records = parsed.hotspots;
    } else if (
      Array.isArray(parsed.locations)
    ) {
      records = parsed.locations;
    } else if (
      Array.isArray(parsed.data)
    ) {
      records = parsed.data;
    }

    const loadedHotspots = records
      .map((record) => {
        return {
          hotspot_id: cleanString(
            record.hotspot_id,
            30
          ),

          name: cleanString(
            record.name ||
              record.location ||
              record.suburb ||
              record.area,
            80
          ),

          metro: cleanString(
            record.metro ||
              record.city ||
              record.province ||
              "South Africa",
            80
          ),

          main_peril: cleanString(
            record.main_peril ||
              "Unknown",
            80
          )
        };
      })
      .filter((record) => {
        return (
          record.hotspot_id &&
          record.name
        );
      });

    if (!loadedHotspots.length) {
      throw new Error(
        "The hotspot file contains no valid locations."
      );
    }

    console.log(
      `Loaded ${loadedHotspots.length} feedback locations.`
    );

    return loadedHotspots;
  } catch (error) {
    console.error(
      "Could not load hotspot locations."
    );

    console.error(
      error.message
    );

    console.error(
      `Expected file: ${HOTSPOTS_FILE}`
    );

    return [];
  }
}


const hotspots = loadHotspots();


function findHotspot(hotspotId) {
  return hotspots.find(
    (hotspot) => {
      return (
        hotspot.hotspot_id ===
        hotspotId
      );
    }
  );
}


function validateFeedback(body) {
  const hotspotId = cleanString(
    body.hotspot_id,
    30
  );

  const hotspot = findHotspot(
    hotspotId
  );

  if (!hotspot) {
    return {
      valid: false,
      message:
        "Please select a valid Sentinel location."
    };
  }

  const rating = Number(
    body.rating
  );

  if (
    !Number.isInteger(rating) ||
    rating < 1 ||
    rating > 5
  ) {
    return {
      valid: false,
      message:
        "Rating must be a whole number between 1 and 5."
    };
  }

  const category = cleanString(
    body.category,
    80
  );

  if (
    !VALID_CATEGORIES.includes(
      category
    )
  ) {
    return {
      valid: false,
      message:
        "Please select a valid feedback category."
    };
  }

  const feedbackText = cleanString(
    body.feedback_text,
    MAX_FEEDBACK_LENGTH
  );

  if (
    feedbackText.length <
    MIN_FEEDBACK_LENGTH
  ) {
    return {
      valid: false,
      message:
        `Feedback must contain at least ${MIN_FEEDBACK_LENGTH} characters.`
    };
  }

  return {
    valid: true,

    value: {
      hotspot,
      rating,
      category,
      feedbackText
    }
  };
}


function createFeedbackRecord(
  validatedFeedback,
  requestBody
) {
  const {
    hotspot,
    rating,
    category,
    feedbackText
  } = validatedFeedback;

  return {
    feedback_id:
      `REAL-${crypto.randomUUID()}`,

    submitted_at:
      new Date().toISOString(),

    user_id:
      cleanString(
        requestBody.user_id,
        100
      ) || "anonymous",

    hotspot_id:
      hotspot.hotspot_id,

    location:
      hotspot.name,

    metro:
      hotspot.metro,

    rating,

    category,

    feedback_text:
      feedbackText,

    platform:
      cleanString(
        requestBody.platform,
        40
      ) || "Web",

    status: "new",

    source: "real"
  };
}


let writeQueue = Promise.resolve();


function saveFeedback(record) {
  const line =
    `${JSON.stringify(record)}\n`;

  const operation = writeQueue.then(
    async () => {
      await fs.promises.mkdir(
        DATA_DIR,
        {
          recursive: true
        }
      );

      await fs.promises.appendFile(
        LIVE_FEEDBACK_FILE,
        line,
        "utf8"
      );
    }
  );

  writeQueue = operation.catch(
    () => {}
  );

  return operation;
}


async function readLiveFeedback() {
  try {
    const contents =
      await fs.promises.readFile(
        LIVE_FEEDBACK_FILE,
        "utf8"
      );

    const records = [];

    const lines = contents
      .split(/\r?\n/)
      .filter(Boolean);

    for (const line of lines) {
      try {
        records.push(
          JSON.parse(line)
        );
      } catch {
        console.warn(
          "Skipped an invalid feedback record."
        );
      }
    }

    return records;
  } catch (error) {
    if (error.code === "ENOENT") {
      return [];
    }

    throw error;
  }
}


async function readTopics() {
  try {
    const contents =
      await fs.promises.readFile(
        TOPICS_FILE,
        "utf8"
      );

    return JSON.parse(contents);
  } catch (error) {
    if (error.code === "ENOENT") {
      return null;
    }

    throw error;
  }
}


app.use(
  (
    request,
    response,
    next
  ) => {
    response.setHeader(
      "X-Content-Type-Options",
      "nosniff"
    );

    response.setHeader(
      "X-Frame-Options",
      "DENY"
    );

    response.setHeader(
      "Referrer-Policy",
      "same-origin"
    );

    next();
  }
);


app.get(
  "/health",
  async (
    request,
    response
  ) => {
    try {
      const feedback =
        await readLiveFeedback();

      response.status(200).json({
        status: "ok",
        locations:
          hotspots.length,
        live_feedback_records:
          feedback.length
      });
    } catch (error) {
      console.error(
        "Health check failed:",
        error
      );

      response.status(500).json({
        status: "error",
        locations:
          hotspots.length,
        live_feedback_records: 0
      });
    }
  }
);


app.get(
  "/api/locations",
  (
    request,
    response
  ) => {
    response.status(200).json({
      locations: hotspots
    });
  }
);


app.get(
  "/api/categories",
  (
    request,
    response
  ) => {
    response.status(200).json({
      categories:
        VALID_CATEGORIES
    });
  }
);


app.get(
  "/api/topics",
  async (
    request,
    response
  ) => {
    try {
      const topics =
        await readTopics();

      if (!topics) {
        response.status(404).json({
          error:
            "No trained topic model is available."
        });

        return;
      }

      response.status(200).json(
        topics
      );
    } catch (error) {
      console.error(
        "Could not read topics:",
        error
      );

      response.status(500).json({
        error:
          "Could not load LDA topic results."
      });
    }
  }
);


app.get(
  "/api/feedback/stats",
  async (
    request,
    response
  ) => {
    try {
      const feedback =
        await readLiveFeedback();

      const categoryCounts = {};
      const locationCounts = {};

      const ratingCounts = {
        1: 0,
        2: 0,
        3: 0,
        4: 0,
        5: 0
      };

      let ratingTotal = 0;

      for (const record of feedback) {
        const category =
          record.category ||
          "Other";

        const location =
          record.location ||
          "Unknown";

        categoryCounts[category] =
          (
            categoryCounts[
              category
            ] || 0
          ) + 1;

        locationCounts[location] =
          (
            locationCounts[
              location
            ] || 0
          ) + 1;

        const rating = Number(
          record.rating
        );

        if (
          Number.isInteger(rating) &&
          rating >= 1 &&
          rating <= 5
        ) {
          ratingCounts[rating] += 1;
          ratingTotal += rating;
        }
      }

      const averageRating =
        feedback.length
          ? ratingTotal /
            feedback.length
          : 0;

      response.status(200).json({
        total_feedback:
          feedback.length,

        average_rating:
          Number(
            averageRating.toFixed(2)
          ),

        rating_counts:
          ratingCounts,

        category_counts:
          categoryCounts,

        location_counts:
          locationCounts
      });
    } catch (error) {
      console.error(
        "Could not calculate feedback statistics:",
        error
      );

      response.status(500).json({
        error:
          "Could not calculate feedback statistics."
      });
    }
  }
);


app.post(
  "/api/feedback",
  async (
    request,
    response
  ) => {
    const validation =
      validateFeedback(
        request.body || {}
      );

    if (!validation.valid) {
      response.status(400).json({
        error:
          validation.message
      });

      return;
    }

    const feedbackRecord =
      createFeedbackRecord(
        validation.value,
        request.body
      );

    try {
      await saveFeedback(
        feedbackRecord
      );

      response.status(201).json({
        message:
          "Thank you. Your feedback was submitted successfully.",

        feedback_id:
          feedbackRecord.feedback_id,

        submitted_at:
          feedbackRecord.submitted_at
      });
    } catch (error) {
      console.error(
        "Could not save feedback:",
        error
      );

      response.status(500).json({
        error:
          "Your feedback could not be saved."
      });
    }
  }
);


app.get(
  "/",
  (
    request,
    response
  ) => {
    response.sendFile(
      CLIENT_FILE
    );
  }
);


app.get(
  "/client.html",
  (
    request,
    response
  ) => {
    response.sendFile(
      CLIENT_FILE
    );
  }
);


app.use(
  (
    request,
    response
  ) => {
    response.status(404).json({
      error: "Not found"
    });
  }
);


app.use(
  (
    error,
    request,
    response,
    next
  ) => {
    console.error(
      "Unexpected server error:",
      error
    );

    if (
      error instanceof SyntaxError
    ) {
      response.status(400).json({
        error:
          "The request body contains invalid JSON."
      });

      return;
    }

    response.status(500).json({
      error:
        "An unexpected server error occurred."
    });
  }
);


app.listen(
  PORT,
  HOST,
  () => {
    console.log(
      `Feedback service listening on http://${HOST}:${PORT}`
    );

    console.log(
      `Available locations: ${hotspots.length}`
    );

    console.log(
      `Live feedback file: ${LIVE_FEEDBACK_FILE}`
    );
  }
);