import { describe, it, expect } from "vitest";
import {
  qcmImageRef,
  resolveQcmImageSrc,
  collectReferencedQcmImageIds,
  pruneQcmImages,
  generateQcmImageId,
} from "./qcm-image-utils";
import type { QCMFile } from "./qcm-types";

const DATA_URL = "data:image/png;base64,AAAA";

function qcmWith(images: Record<string, string>, refs: string[]): QCMFile {
  return {
    version: 1,
    images,
    chapters: [
      {
        id: "ch1",
        title: "C",
        questions: [
          {
            id: "q1",
            text: `Question ${refs[0] ? `![](${refs[0]})` : ""}`,
            answers: [
              { id: "a1", text: refs[1] ? `![](${refs[1]})` : "yes", correct: true },
              { id: "a2", text: "no", correct: false },
            ],
            explanation: refs[2] ? `Because ![](${refs[2]})` : undefined,
          },
        ],
      },
    ],
  };
}

describe("qcmImageRef", () => {
  it("prefixes the id with qcmimg:", () => {
    expect(qcmImageRef("img_abc")).toBe("qcmimg:img_abc");
  });
});

describe("generateQcmImageId", () => {
  it("produces unique prefixed ids", () => {
    const a = generateQcmImageId();
    const b = generateQcmImageId();
    expect(a).not.toBe(b);
    expect(a.startsWith("img_")).toBe(true);
  });
});

describe("resolveQcmImageSrc", () => {
  const images = { img_x: DATA_URL };

  it("resolves a qcmimg ref to its data URL", () => {
    expect(resolveQcmImageSrc("qcmimg:img_x", images)).toBe(DATA_URL);
  });

  it("returns null for a missing ref", () => {
    expect(resolveQcmImageSrc("qcmimg:nope", images)).toBeNull();
  });

  it("passes through data and http(s) URLs", () => {
    expect(resolveQcmImageSrc(DATA_URL, images)).toBe(DATA_URL);
    expect(resolveQcmImageSrc("https://e/x.png", images)).toBe("https://e/x.png");
  });

  it("returns null for empty or unknown relative src", () => {
    expect(resolveQcmImageSrc("", images)).toBeNull();
    expect(resolveQcmImageSrc("foo.png", images)).toBeNull();
    expect(resolveQcmImageSrc(undefined, images)).toBeNull();
  });
});

describe("collectReferencedQcmImageIds", () => {
  it("finds ids across question, answer and explanation text", () => {
    const qcm = qcmWith(
      { a: DATA_URL, b: DATA_URL, c: DATA_URL },
      ["qcmimg:a", "qcmimg:b", "qcmimg:c"],
    );
    expect(collectReferencedQcmImageIds(qcm)).toEqual(new Set(["a", "b", "c"]));
  });
});

describe("pruneQcmImages", () => {
  it("drops images no longer referenced", () => {
    const qcm = qcmWith({ a: DATA_URL, orphan: DATA_URL }, ["qcmimg:a"]);
    const pruned = pruneQcmImages(qcm);
    expect(pruned.images).toEqual({ a: DATA_URL });
  });

  it("removes the images field entirely when nothing is referenced", () => {
    const qcm = qcmWith({ orphan: DATA_URL }, []);
    const pruned = pruneQcmImages(qcm);
    expect(pruned.images).toBeUndefined();
  });

  it("is a no-op when there are no images", () => {
    const qcm = qcmWith({}, []);
    const pruned = pruneQcmImages(qcm);
    expect(pruned.images).toBeUndefined();
  });
});
