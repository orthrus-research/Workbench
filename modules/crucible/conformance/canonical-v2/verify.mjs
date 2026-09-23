#!/usr/bin/env node

import { createHash } from "node:crypto";
import { readFileSync } from "node:fs";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";
import { isProxy } from "node:util/types";

export const CANONICALIZER = "workbench-canonical-json-v2";
export const CONTENT_DOMAIN = "workbench-content-v2\n";
export const INT64_MIN = -(1n << 63n);
export const INT64_MAX = (1n << 63n) - 1n;
export const MAX_NESTING_DEPTH = 256;

const KIND_PATTERN = /^[a-z][a-z0-9-]*$/;
const HEX_64_PATTERN = /^[0-9a-f]{64}$/;
const UTF8_DECODER = new TextDecoder("utf-8", { fatal: true, ignoreBOM: true });

export class ConformanceError extends Error {
  constructor(code, message) {
    super(message);
    this.name = "ConformanceError";
    this.code = code;
  }
}

function fail(code, message) {
  throw new ConformanceError(code, message);
}

function isWhitespace(character) {
  return character === " " || character === "\t" || character === "\r" || character === "\n";
}

function isDigit(character) {
  return character >= "0" && character <= "9";
}

function isHighSurrogate(codeUnit) {
  return codeUnit >= 0xd800 && codeUnit <= 0xdbff;
}

function isLowSurrogate(codeUnit) {
  return codeUnit >= 0xdc00 && codeUnit <= 0xdfff;
}

class StrictJsonParser {
  constructor(source) {
    this.source = source;
    this.index = 0;
  }

  parse() {
    this.skipWhitespace();
    const value = this.parseValue(0);
    this.skipWhitespace();
    if (this.index !== this.source.length) {
      fail("trailing-data", `unexpected data at UTF-16 offset ${this.index}`);
    }
    return value;
  }

  skipWhitespace() {
    while (this.index < this.source.length && isWhitespace(this.source[this.index])) {
      this.index += 1;
    }
  }

  parseValue(containerDepth) {
    const character = this.source[this.index];
    if (character === "{" || character === "[") {
      const nextDepth = containerDepth + 1;
      if (nextDepth > MAX_NESTING_DEPTH) {
        fail("nesting-limit-exceeded", `container nesting exceeds ${MAX_NESTING_DEPTH} at UTF-16 offset ${this.index}`);
      }
      if (character === "{") return this.parseObject(nextDepth);
      return this.parseArray(nextDepth);
    }
    if (character === '"') return this.parseString();
    if (character === "t") return this.parseLiteral("true", true);
    if (character === "f") return this.parseLiteral("false", false);
    if (character === "n") return this.parseLiteral("null", null);
    if (character === "-" || isDigit(character)) return this.parseInteger();
    fail("unexpected-token", `unexpected token at UTF-16 offset ${this.index}`);
  }

  parseLiteral(token, value) {
    if (!this.source.startsWith(token, this.index)) {
      fail("unexpected-token", `invalid literal at UTF-16 offset ${this.index}`);
    }
    this.index += token.length;
    return value;
  }

  parseObject(containerDepth) {
    this.index += 1;
    const result = Object.create(null);
    const keys = new Set();
    this.skipWhitespace();
    if (this.source[this.index] === "}") {
      this.index += 1;
      return result;
    }

    while (true) {
      if (this.source[this.index] !== '"') {
        fail("object-key-required", `object key required at UTF-16 offset ${this.index}`);
      }
      const key = this.parseString();
      if (keys.has(key)) {
        fail("duplicate-key", `duplicate object key ${JSON.stringify(key)}`);
      }
      keys.add(key);
      this.skipWhitespace();
      if (this.source[this.index] !== ":") {
        fail("colon-required", `colon required at UTF-16 offset ${this.index}`);
      }
      this.index += 1;
      this.skipWhitespace();
      Object.defineProperty(result, key, {
        configurable: false,
        enumerable: true,
        value: this.parseValue(containerDepth),
        writable: false,
      });
      this.skipWhitespace();
      const delimiter = this.source[this.index];
      if (delimiter === "}") {
        this.index += 1;
        return result;
      }
      if (delimiter !== ",") {
        fail("object-delimiter-required", `comma or closing brace required at UTF-16 offset ${this.index}`);
      }
      this.index += 1;
      this.skipWhitespace();
    }
  }

  parseArray(containerDepth) {
    this.index += 1;
    const result = [];
    this.skipWhitespace();
    if (this.source[this.index] === "]") {
      this.index += 1;
      return result;
    }

    while (true) {
      result.push(this.parseValue(containerDepth));
      this.skipWhitespace();
      const delimiter = this.source[this.index];
      if (delimiter === "]") {
        this.index += 1;
        return result;
      }
      if (delimiter !== ",") {
        fail("array-delimiter-required", `comma or closing bracket required at UTF-16 offset ${this.index}`);
      }
      this.index += 1;
      this.skipWhitespace();
    }
  }

  parseString() {
    this.index += 1;
    let result = "";
    while (this.index < this.source.length) {
      const character = this.source[this.index];
      this.index += 1;
      if (character === '"') return result;
      if (character === "\\") {
        result += this.parseEscape();
        continue;
      }

      const codeUnit = character.charCodeAt(0);
      if (codeUnit <= 0x1f) {
        fail("unescaped-control", `unescaped control character at UTF-16 offset ${this.index - 1}`);
      }
      if (isHighSurrogate(codeUnit)) {
        if (this.index >= this.source.length || !isLowSurrogate(this.source.charCodeAt(this.index))) {
          fail("unpaired-surrogate", `unpaired high surrogate at UTF-16 offset ${this.index - 1}`);
        }
        result += character + this.source[this.index];
        this.index += 1;
        continue;
      }
      if (isLowSurrogate(codeUnit)) {
        fail("unpaired-surrogate", `unpaired low surrogate at UTF-16 offset ${this.index - 1}`);
      }
      result += character;
    }
    fail("unterminated-string", "unterminated string");
  }

  parseEscape() {
    if (this.index >= this.source.length) {
      fail("unterminated-string", "unterminated string escape");
    }
    const escape = this.source[this.index];
    this.index += 1;
    const shortEscapes = {
      '"': '"',
      "\\": "\\",
      "/": "/",
      b: "\b",
      f: "\f",
      n: "\n",
      r: "\r",
      t: "\t",
    };
    if (Object.hasOwn(shortEscapes, escape)) return shortEscapes[escape];
    if (escape !== "u") {
      fail("invalid-escape", `invalid string escape at UTF-16 offset ${this.index - 1}`);
    }

    const first = this.readHexCodeUnit();
    if (isLowSurrogate(first)) {
      fail("unpaired-surrogate", `unpaired escaped low surrogate at UTF-16 offset ${this.index - 4}`);
    }
    if (!isHighSurrogate(first)) return String.fromCharCode(first);

    if (this.source[this.index] !== "\\" || this.source[this.index + 1] !== "u") {
      fail("unpaired-surrogate", `escaped high surrogate lacks a low surrogate at UTF-16 offset ${this.index - 4}`);
    }
    this.index += 2;
    const second = this.readHexCodeUnit();
    if (!isLowSurrogate(second)) {
      fail("unpaired-surrogate", `escaped high surrogate has an invalid pair at UTF-16 offset ${this.index - 4}`);
    }
    const codePoint = 0x10000 + ((first - 0xd800) << 10) + (second - 0xdc00);
    return String.fromCodePoint(codePoint);
  }

  readHexCodeUnit() {
    const digits = this.source.slice(this.index, this.index + 4);
    if (!/^[0-9a-fA-F]{4}$/.test(digits)) {
      fail("invalid-unicode-escape", `invalid Unicode escape at UTF-16 offset ${this.index}`);
    }
    this.index += 4;
    return Number.parseInt(digits, 16);
  }

  parseInteger() {
    const start = this.index;
    if (this.source[this.index] === "-") this.index += 1;
    if (!isDigit(this.source[this.index])) {
      fail("invalid-number", `integer digit required at UTF-16 offset ${this.index}`);
    }

    if (this.source[this.index] === "0") {
      this.index += 1;
      if (isDigit(this.source[this.index])) {
        fail("invalid-number", `leading zero at UTF-16 offset ${this.index}`);
      }
    } else {
      while (isDigit(this.source[this.index])) this.index += 1;
    }

    if (this.source[this.index] === "." || this.source[this.index] === "e" || this.source[this.index] === "E") {
      fail("floating-point-forbidden", `floating-point JSON number at UTF-16 offset ${start}`);
    }

    let value;
    try {
      value = BigInt(this.source.slice(start, this.index));
    } catch {
      fail("invalid-number", `invalid integer at UTF-16 offset ${start}`);
    }
    if (value < INT64_MIN || value > INT64_MAX) {
      fail("integer-out-of-range", `integer outside the signed 64-bit domain at UTF-16 offset ${start}`);
    }
    return value;
  }
}

function decodeUtf8(bytes) {
  try {
    return UTF8_DECODER.decode(bytes);
  } catch {
    fail("invalid-utf8", "input is not well-formed UTF-8");
  }
}

export function parseJsonDomainBytes(bytes) {
  return new StrictJsonParser(decodeUtf8(bytes)).parse();
}

export function parseJsonDomainText(text) {
  return parseJsonDomainBytes(Buffer.from(text, "utf8"));
}

function assertScalarString(value) {
  for (let index = 0; index < value.length; index += 1) {
    const codeUnit = value.charCodeAt(index);
    if (isHighSurrogate(codeUnit)) {
      if (index + 1 >= value.length || !isLowSurrogate(value.charCodeAt(index + 1))) {
        fail("unpaired-surrogate", `string contains an unpaired high surrogate at UTF-16 offset ${index}`);
      }
      index += 1;
    } else if (isLowSurrogate(codeUnit)) {
      fail("unpaired-surrogate", `string contains an unpaired low surrogate at UTF-16 offset ${index}`);
    }
  }
}

function encodeString(value) {
  assertScalarString(value);
  let result = '"';
  for (const character of value) {
    const codePoint = character.codePointAt(0);
    if (codePoint === 0x22) {
      result += '\\"';
    } else if (codePoint === 0x5c) {
      result += "\\\\";
    } else if (codePoint <= 0x1f) {
      result += `\\u00${codePoint.toString(16).padStart(2, "0")}`;
    } else {
      result += character;
    }
  }
  return `${result}"`;
}

function compareUtf8(left, right) {
  return Buffer.compare(Buffer.from(left, "utf8"), Buffer.from(right, "utf8"));
}

function inspectObjectEntries(value, path) {
  if (isProxy(value)) {
    fail("proxy-forbidden", `${path}: Proxy objects are outside the canonical value domain`);
  }
  const prototype = Object.getPrototypeOf(value);
  if (prototype !== null && prototype !== Object.prototype) {
    fail("object-prototype-invalid", `${path}: object must have null or exact Object.prototype`);
  }

  const entries = [];
  for (const key of Reflect.ownKeys(value)) {
    if (typeof key === "symbol") {
      fail("symbol-key-forbidden", `${path}: symbol-keyed properties are outside the JSON object domain`);
    }
    assertScalarString(key);
    const descriptor = Object.getOwnPropertyDescriptor(value, key);
    if (descriptor === undefined) {
      fail("object-property-invalid", `${path}: own property ${JSON.stringify(key)} disappeared during inspection`);
    }
    if (!("value" in descriptor)) {
      fail("object-accessor-forbidden", `${path}: accessor property ${JSON.stringify(key)} is forbidden`);
    }
    if (!descriptor.enumerable) {
      fail("object-property-invalid", `${path}: non-enumerable property ${JSON.stringify(key)} is forbidden`);
    }
    entries.push([key, descriptor.value]);
  }
  entries.sort((left, right) => compareUtf8(left[0], right[0]));
  return entries;
}

function inspectArrayItems(value, path) {
  if (isProxy(value)) {
    fail("proxy-forbidden", `${path}: Proxy arrays are outside the canonical value domain`);
  }
  if (Object.getPrototypeOf(value) !== Array.prototype) {
    fail("array-prototype-invalid", `${path}: array must have exact Array.prototype`);
  }

  const lengthDescriptor = Object.getOwnPropertyDescriptor(value, "length");
  if (lengthDescriptor === undefined || !("value" in lengthDescriptor)) {
    fail("array-property-forbidden", `${path}: array length must be an own data property`);
  }
  const length = lengthDescriptor.value;
  const entries = [];
  for (const key of Reflect.ownKeys(value)) {
    if (key === "length") continue;
    if (typeof key === "symbol") {
      fail("symbol-key-forbidden", `${path}: symbol-keyed array properties are forbidden`);
    }
    if (!/^(?:0|[1-9][0-9]*)$/.test(key) || BigInt(key) >= BigInt(length)) {
      fail("array-property-forbidden", `${path}: extra array property ${JSON.stringify(key)} is forbidden`);
    }
    const descriptor = Object.getOwnPropertyDescriptor(value, key);
    if (descriptor === undefined) {
      fail("array-property-forbidden", `${path}: array property ${key} disappeared during inspection`);
    }
    if (!("value" in descriptor)) {
      fail("array-accessor-forbidden", `${path}: accessor array element ${key} is forbidden`);
    }
    if (!descriptor.enumerable) {
      fail("array-property-forbidden", `${path}: non-enumerable array element ${key} is forbidden`);
    }
    entries.push([Number(key), descriptor.value]);
  }
  if (entries.length !== length) {
    fail("sparse-array-forbidden", `${path}: sparse arrays are outside the canonical value domain`);
  }
  entries.sort((left, right) => left[0] - right[0]);
  for (let index = 0; index < entries.length; index += 1) {
    if (entries[index][0] !== index) {
      fail("sparse-array-forbidden", `${path}: missing array element ${index}`);
    }
  }
  return entries.map((entry) => entry[1]);
}

function canonicalizeAt(value, path, ancestors, containerDepth) {
  if (value === null) return "null";
  if (typeof value === "boolean") return value ? "true" : "false";
  if (typeof value === "string") return encodeString(value);
  if (typeof value === "bigint") {
    if (value < INT64_MIN || value > INT64_MAX) {
      fail("integer-out-of-range", "integer outside the signed 64-bit domain");
    }
    return value.toString(10);
  }
  if (typeof value === "number") {
    fail("javascript-number-forbidden", "use bigint for identity-bearing integers; JavaScript numbers are not accepted");
  }
  if (typeof value === "object" && isProxy(value)) {
    fail("proxy-forbidden", `${path}: Proxy values are outside the canonical value domain`);
  }
  if (Array.isArray(value)) {
    const nextDepth = containerDepth + 1;
    if (nextDepth > MAX_NESTING_DEPTH) {
      fail("nesting-limit-exceeded", `${path}: container nesting exceeds ${MAX_NESTING_DEPTH}`);
    }
    if (ancestors.has(value)) {
      fail("cyclic-value", `${path}: cyclic array is outside the canonical value domain`);
    }
    const items = inspectArrayItems(value, path);
    ancestors.add(value);
    try {
      return `[${items
        .map((entry, index) => canonicalizeAt(entry, `${path}[${index}]`, ancestors, nextDepth))
        .join(",")}]`;
    } finally {
      ancestors.delete(value);
    }
  }
  if (typeof value === "object") {
    const nextDepth = containerDepth + 1;
    if (nextDepth > MAX_NESTING_DEPTH) {
      fail("nesting-limit-exceeded", `${path}: container nesting exceeds ${MAX_NESTING_DEPTH}`);
    }
    if (ancestors.has(value)) {
      fail("cyclic-value", `${path}: cyclic object is outside the canonical value domain`);
    }
    const entries = inspectObjectEntries(value, path);
    ancestors.add(value);
    try {
      return `{${entries
        .map(
          ([key, entry]) =>
            `${encodeString(key)}:${canonicalizeAt(entry, `${path}[${JSON.stringify(key)}]`, ancestors, nextDepth)}`,
        )
        .join(",")}}`;
    } finally {
      ancestors.delete(value);
    }
  }
  fail("unsupported-value", `unsupported JavaScript value type ${typeof value}`);
}

export function canonicalize(value) {
  return canonicalizeAt(value, "$", new Set(), 0);
}

export function validateCanonicalJsonBytes(bytes) {
  const value = parseJsonDomainBytes(bytes);
  const canonicalBytes = Buffer.from(canonicalize(value), "utf8");
  if (!Buffer.from(bytes).equals(canonicalBytes)) {
    fail("noncanonical-bytes", "input JSON bytes do not equal their canonical V2 encoding");
  }
  return value;
}

function bodyWithoutId(entries) {
  const body = Object.create(null);
  for (const [key, value] of entries) {
    if (key !== "id") {
      Object.defineProperty(body, key, {
        configurable: false,
        enumerable: true,
        value,
        writable: false,
      });
    }
  }
  return body;
}

function validateSemanticHeader(record) {
  if (record === null || typeof record !== "object" || Array.isArray(record)) {
    fail("record-object-required", "an identity-bearing record must be a JSON object");
  }
  const entries = inspectObjectEntries(record, "$record");
  const fields = new Map(entries);
  const kind = fields.get("kind");
  if (typeof kind !== "string") {
    fail("record-kind-required", "record kind must be a string");
  }
  if (!KIND_PATTERN.test(kind)) {
    fail("record-kind-invalid", "record kind must match [a-z][a-z0-9-]*");
  }
  const format = fields.get("format");
  if (typeof format !== "string" || format.length === 0) {
    fail("record-format-required", "record format must be a nonempty string");
  }
  if (fields.get("canonicalizer") !== CANONICALIZER) {
    fail("record-canonicalizer-invalid", `record canonicalizer must be ${CANONICALIZER}`);
  }
  return { entries, fields, kind };
}

export function computeContentIdentity(record, { requireId = false } = {}) {
  const { entries, fields, kind } = validateSemanticHeader(record);
  const body = bodyWithoutId(entries);
  const canonicalBody = Buffer.from(canonicalize(body), "utf8");
  const preimage = Buffer.concat([
    Buffer.from(CONTENT_DOMAIN, "utf8"),
    Buffer.from(kind, "utf8"),
    Buffer.from("\n", "utf8"),
    canonicalBody,
  ]);
  const digest = createHash("sha256").update(preimage).digest("hex");
  const contentId = `${kind}:sha256:${digest}`;
  const actualId = fields.get("id");

  if (requireId && typeof actualId !== "string") {
    fail("record-id-required", "fixture record must carry its expected content ID");
  }
  if (typeof actualId === "string") {
    const [idKind, algorithm, idDigest, ...remainder] = actualId.split(":");
    if (remainder.length !== 0 || idKind !== kind || algorithm !== "sha256" || !HEX_64_PATTERN.test(idDigest ?? "")) {
      fail("record-id-invalid", "record ID must be <matching-kind>:sha256:<64 lowercase hexadecimal characters>");
    }
    if (actualId !== contentId) {
      fail("content-id-mismatch", `record ID does not match canonical content; expected ${contentId}`);
    }
  }

  return {
    canonicalBody,
    contentId,
    digest,
    preimage,
    canonicalBodySha256: createHash("sha256").update(canonicalBody).digest("hex"),
  };
}

function bytesForVector(vector) {
  const hasText = Object.hasOwn(vector, "input_json");
  const hasHex = Object.hasOwn(vector, "input_utf8_hex");
  if (hasText === hasHex) {
    fail("fixture-source-invalid", `${vector.name}: exactly one of input_json or input_utf8_hex is required`);
  }
  if (hasText) {
    if (typeof vector.input_json !== "string") {
      fail("fixture-source-invalid", `${vector.name}: input_json must be a string`);
    }
    return Buffer.from(vector.input_json, "utf8");
  }
  if (typeof vector.input_utf8_hex !== "string" || !/^(?:[0-9a-f]{2})*$/.test(vector.input_utf8_hex)) {
    fail("fixture-source-invalid", `${vector.name}: input_utf8_hex must be even-length lowercase hexadecimal`);
  }
  return Buffer.from(vector.input_utf8_hex, "hex");
}

function assertEqual(actual, expected, label) {
  if (actual !== expected) {
    throw new Error(`${label}: expected ${expected}, received ${actual}`);
  }
}

function assertConformanceFailure(expectedCode, action, label) {
  let caught;
  try {
    action();
  } catch (error) {
    caught = error;
  }
  if (!(caught instanceof ConformanceError)) {
    throw new Error(`${label}: expected ConformanceError ${expectedCode}, received ${caught ?? "no error"}`);
  }
  assertEqual(caught.code, expectedCode, label);
}

export function verifyApiHardening() {
  let checks = 0;
  const expect = (code, action, label) => {
    assertConformanceFailure(code, action, label);
    checks += 1;
  };

  assertEqual(canonicalize({ b: 2n, a: 1n }), '{"a":1,"b":2}', "exact plain object acceptance");
  checks += 1;
  assertEqual(
    canonicalize(parseJsonDomainText('{"b":2,"a":1}')),
    '{"a":1,"b":2}',
    "parser-produced null-prototype object acceptance",
  );
  checks += 1;

  const customPrototype = Object.create({ inherited: 1n });
  customPrototype.value = 1n;
  expect(
    "object-prototype-invalid",
    () => canonicalize(customPrototype),
    "custom object prototype rejection",
  );

  let objectGetterInvoked = false;
  const accessorObject = {};
  Object.defineProperty(accessorObject, "value", {
    enumerable: true,
    get() {
      objectGetterInvoked = true;
      return 1n;
    },
  });
  expect(
    "object-accessor-forbidden",
    () => canonicalize(accessorObject),
    "object accessor rejection",
  );
  assertEqual(objectGetterInvoked, false, "object getter was not invoked");
  checks += 1;

  const nonEnumerableObject = {};
  Object.defineProperty(nonEnumerableObject, "hidden", { enumerable: false, value: 1n });
  expect(
    "object-property-invalid",
    () => canonicalize(nonEnumerableObject),
    "non-enumerable object property rejection",
  );

  const symbolObject = { value: 1n };
  symbolObject[Symbol("hidden")] = 2n;
  expect("symbol-key-forbidden", () => canonicalize(symbolObject), "symbol object key rejection");

  const sparse = [];
  sparse.length = 1;
  expect("sparse-array-forbidden", () => canonicalize(sparse), "sparse array rejection");

  const extendedArray = [1n];
  extendedArray.extra = 2n;
  expect(
    "array-property-forbidden",
    () => canonicalize(extendedArray),
    "extra array property rejection",
  );

  let arrayGetterInvoked = false;
  const accessorArray = new Array(1);
  Object.defineProperty(accessorArray, "0", {
    enumerable: true,
    get() {
      arrayGetterInvoked = true;
      return 1n;
    },
  });
  expect(
    "array-accessor-forbidden",
    () => canonicalize(accessorArray),
    "array accessor rejection",
  );
  assertEqual(arrayGetterInvoked, false, "array getter was not invoked");
  checks += 1;

  class ArraySubclass extends Array {}
  expect(
    "array-prototype-invalid",
    () => canonicalize(new ArraySubclass(1n)),
    "array subclass rejection",
  );

  const symbolArray = [1n];
  symbolArray[Symbol("hidden")] = 2n;
  expect("symbol-key-forbidden", () => canonicalize(symbolArray), "symbol array key rejection");

  const cyclicObject = {};
  cyclicObject.self = cyclicObject;
  expect("cyclic-value", () => canonicalize(cyclicObject), "cyclic object rejection");
  const cyclicArray = [];
  cyclicArray.push(cyclicArray);
  expect("cyclic-value", () => canonicalize(cyclicArray), "cyclic array rejection");

  let deeplyNested = null;
  for (let depth = 0; depth <= MAX_NESTING_DEPTH; depth += 1) deeplyNested = [deeplyNested];
  expect(
    "nesting-limit-exceeded",
    () => canonicalize(deeplyNested),
    "encoder nesting limit",
  );
  const deepJson = `${"[".repeat(MAX_NESTING_DEPTH + 1)}null${"]".repeat(MAX_NESTING_DEPTH + 1)}`;
  expect(
    "nesting-limit-exceeded",
    () => parseJsonDomainText(deepJson),
    "parser nesting limit",
  );

  let proxyGetterInvoked = false;
  const proxy = new Proxy(
    { value: 1n },
    {
      get(target, key, receiver) {
        proxyGetterInvoked = true;
        return Reflect.get(target, key, receiver);
      },
    },
  );
  expect("proxy-forbidden", () => canonicalize(proxy), "Proxy rejection");
  assertEqual(proxyGetterInvoked, false, "Proxy getter trap was not invoked");
  checks += 1;

  let recordGetterInvoked = false;
  const accessorRecord = {
    canonicalizer: CANONICALIZER,
    format: "workbench-canonical-vector-v1",
  };
  Object.defineProperty(accessorRecord, "kind", {
    enumerable: true,
    get() {
      recordGetterInvoked = true;
      return "canonical-vector";
    },
  });
  expect(
    "object-accessor-forbidden",
    () => computeContentIdentity(accessorRecord),
    "record accessor rejection",
  );
  assertEqual(recordGetterInvoked, false, "record getter was not invoked");
  checks += 1;

  return checks;
}

function loadFixture(path) {
  const bytes = readFileSync(path);
  let fixture;
  try {
    fixture = JSON.parse(decodeUtf8(bytes));
  } catch (error) {
    if (error instanceof ConformanceError) throw error;
    throw new Error(`${path}: fixture manifest is not valid JSON: ${error.message}`);
  }
  return fixture;
}

function verifyManifestHeader(manifest, expectedFormat) {
  assertEqual(manifest.format, expectedFormat, `${expectedFormat} format`);
  assertEqual(manifest.canonicalizer, CANONICALIZER, `${expectedFormat} canonicalizer`);
  assertEqual(manifest.max_nesting_depth, MAX_NESTING_DEPTH, `${expectedFormat} nesting limit`);
  if (!Array.isArray(manifest.vectors) || manifest.vectors.length === 0) {
    throw new Error(`${expectedFormat}: vectors must be a nonempty array`);
  }
}

export function verifyValidVectors(manifest) {
  verifyManifestHeader(manifest, "workbench-canonical-json-v2-valid-vectors-v1");
  assertEqual(manifest.content_domain_utf8, CONTENT_DOMAIN, "content domain");
  assertEqual(
    manifest.content_domain_utf8_hex,
    Buffer.from(CONTENT_DOMAIN, "utf8").toString("hex"),
    "content domain UTF-8 hex",
  );

  const names = new Set();
  for (const vector of manifest.vectors) {
    if (typeof vector.name !== "string" || vector.name.length === 0 || names.has(vector.name)) {
      throw new Error("valid vector names must be nonempty and unique");
    }
    names.add(vector.name);
    const record = parseJsonDomainBytes(bytesForVector(vector));
    const identity = computeContentIdentity(record, { requireId: true });
    assertEqual(
      identity.canonicalBody.toString("hex"),
      vector.expected_canonical_body_utf8_hex,
      `${vector.name} canonical UTF-8 hex`,
    );
    assertEqual(identity.contentId, vector.expected_content_id, `${vector.name} content ID`);
    assertEqual(
      identity.canonicalBodySha256,
      vector.expected_canonical_body_sha256,
      `${vector.name} undomained canonical-body SHA-256`,
    );
    if (identity.contentId.endsWith(identity.canonicalBodySha256)) {
      throw new Error(`${vector.name}: domain-separated digest unexpectedly equals the bare body digest`);
    }

    const reparsed = validateCanonicalJsonBytes(identity.canonicalBody);
    assertEqual(
      Buffer.from(canonicalize(reparsed), "utf8").toString("hex"),
      vector.expected_canonical_body_utf8_hex,
      `${vector.name} canonical idempotence`,
    );
  }
  return manifest.vectors.length;
}

export function verifyInvalidVectors(manifest) {
  verifyManifestHeader(manifest, "workbench-canonical-json-v2-invalid-vectors-v1");
  const names = new Set();
  for (const vector of manifest.vectors) {
    if (typeof vector.name !== "string" || vector.name.length === 0 || names.has(vector.name)) {
      throw new Error("invalid vector names must be nonempty and unique");
    }
    names.add(vector.name);
    let actualError;
    try {
      const input = bytesForVector(vector);
      if (vector.operation === "validate-canonical-bytes") {
        validateCanonicalJsonBytes(input);
      } else if (vector.operation === undefined || vector.operation === "parse-and-identify-record") {
        const record = parseJsonDomainBytes(input);
        computeContentIdentity(record, { requireId: true });
      } else {
        fail("fixture-operation-invalid", `${vector.name}: unknown operation ${vector.operation}`);
      }
    } catch (error) {
      if (error instanceof ConformanceError) actualError = error.code;
      else throw error;
    }
    if (actualError === undefined) {
      throw new Error(`${vector.name}: invalid vector was accepted`);
    }
    assertEqual(actualError, vector.expected_error, `${vector.name} rejection`);
  }
  return manifest.vectors.length;
}

function main() {
  const fixtureDirectory = dirname(fileURLToPath(import.meta.url));
  verifyApiHardening();
  const valid = loadFixture(join(fixtureDirectory, "valid-vectors.json"));
  const invalid = loadFixture(join(fixtureDirectory, "invalid-vectors.json"));
  const validCount = verifyValidVectors(valid);
  const invalidCount = verifyInvalidVectors(invalid);
  process.stdout.write(
    `canonical-v2 conformance passed: ${validCount} valid vectors, ${invalidCount} invalid vectors\n`,
  );
}

if (process.argv[1] && fileURLToPath(import.meta.url) === process.argv[1]) {
  try {
    main();
  } catch (error) {
    process.stderr.write(`${error.stack ?? error}\n`);
    process.exitCode = 1;
  }
}
