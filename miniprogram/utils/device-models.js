/* 传输设备型号库，来源：docs/传输设备型号.xlsx */

const DEVICE_MODELS = [
  "C600",
  "MA5608T",
  "MA5680T",
  "MA5800-X17",
  "OptiX 2500+(Metro 3000)",
  "OptiX Metro 100",
  "OptiX Metro 1000V3",
  "OptiX Metro 500",
  "OptiX OSN 1500",
  "OptiX OSN 1800",
  "OptiX OSN 1800 I E",
  "OptiX OSN 1800 II E",
  "OptiX OSN 1800 V",
  "OptiX OSN 1800 V Pro",
  "OptiX OSN 3500",
  "OptiX OSN 500",
  "OptiX OSN 8800 T32",
  "OptiX OSN 9800",
  "OptiX OSN 9800 M12",
  "OptiX OSN 9800 M24",
  "OptiX OSN 9800 P32",
  "OptiX OSN 9800 U16",
  "OptiX OSN 9800 U32",
  "OptiX PTN 3900",
  "OptiX PTN 7900-24",
  "OptiX PTN 7900-32",
  "OptiX PTN 7900E-12",
  "OptiX PTN 7900E-24",
  "OptiX PTN 7900E-32",
  "OptiX PTN 906A",
  "OptiX PTN 906B",
  "OptiX PTN 910",
  "OptiX PTN 910-F",
  "OptiX PTN 950",
  "OptiX PTN 960",
  "OptiX PTN 970",
  "OptiX PTN 970C",
  "OptiX PTN 980",
  "OptiX PTN 980B",
];

const DEFAULT_LIMIT = 6;

function normalizeModelText(value) {
  return String(value || "")
    .toUpperCase()
    .replace(/[^0-9A-Z]+/g, "");
}

function tokenizeModelText(value) {
  return String(value || "")
    .toUpperCase()
    .split(/[^0-9A-Z]+/g)
    .filter((token) => token.length >= 2);
}

function commonPrefixLength(left, right) {
  const max = Math.min(left.length, right.length);
  let index = 0;
  while (index < max && left[index] === right[index]) {
    index += 1;
  }
  return index;
}

function scoreModelCandidate(query, model) {
  const normalizedQuery = normalizeModelText(query);
  const normalizedModel = normalizeModelText(model);
  if (!normalizedQuery || !normalizedModel) {
    return 0;
  }

  if (normalizedQuery.length < 2) {
    return normalizedModel === normalizedQuery ? 1000 : 0;
  }

  let score = 0;
  if (normalizedModel === normalizedQuery) {
    score += 1000;
  }
  if (normalizedModel.startsWith(normalizedQuery)) {
    score += 700;
  }
  if (normalizedModel.includes(normalizedQuery)) {
    score += 500 + Math.min(normalizedQuery.length, 20);
  }
  if (normalizedQuery.includes(normalizedModel)) {
    score += 220;
  }

  const tokens = tokenizeModelText(query);
  if (tokens.length) {
    let hits = 0;
    for (const token of tokens) {
      const normalizedToken = normalizeModelText(token);
      if (normalizedToken && normalizedModel.includes(normalizedToken)) {
        hits += 1;
      }
    }
    if (hits) {
      score += hits * 80;
      if (hits === tokens.length && tokens.length > 1) {
        score += 120;
      }
    }
  }

  if (score > 0) {
    score += commonPrefixLength(normalizedQuery, normalizedModel) * 8;
  }
  return score;
}

function suggestDeviceModels(query, limit = DEFAULT_LIMIT) {
  const normalizedQuery = String(query || "").trim();
  if (!normalizedQuery) {
    return [];
  }

  return DEVICE_MODELS
    .map((model) => ({
      model,
      score: scoreModelCandidate(normalizedQuery, model),
      lengthDiff: Math.abs(normalizeModelText(model).length - normalizeModelText(normalizedQuery).length),
    }))
    .filter((item) => item.score > 0)
    .sort((left, right) => (
      right.score - left.score
      || left.lengthDiff - right.lengthDiff
      || left.model.localeCompare(right.model)
    ))
    .slice(0, limit)
    .map((item) => item.model);
}

function pickBestDeviceModel(query) {
  const matches = suggestDeviceModels(query, 1);
  return matches[0] || String(query || "").trim();
}

module.exports = {
  DEVICE_MODELS,
  normalizeModelText,
  suggestDeviceModels,
  pickBestDeviceModel,
};
