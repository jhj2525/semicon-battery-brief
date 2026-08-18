import test from "node:test";
import assert from "node:assert/strict";
import worker from "../cloudflare-worker.js";

const origin = "https://jhj2525.github.io";
const baseEnv = {
  ALLOWED_ORIGIN: origin, ADMIN_PASSWORD: "test-password",
  ADMIN_TOKEN_SECRET: "test-secret-that-is-long-enough-for-hmac-signing",
  GITHUB_OWNER: "jhj2525", GITHUB_REPO: "semicon-battery-brief",
  GITHUB_BRANCH: "main", GITHUB_TOKEN: "test-token",
};

async function post(body, env, token = "") {
  return worker.fetch(new Request("https://worker.example", {
    method: "POST",
    headers: { "Content-Type": "application/json", Origin: origin, ...(token ? { Authorization: `Bearer ${token}` } : {}) },
    body: JSON.stringify(body),
  }), env);
}

async function login(env) {
  const response = await post({ action: "login", password: baseEnv.ADMIN_PASSWORD }, env);
  assert.equal(response.status, 200);
  return (await response.json()).token;
}

test("기존 Origin 검사를 유지한다", async () => {
  const response = await worker.fetch(new Request("https://worker.example", {
    method: "POST", headers: { "Content-Type": "application/json", Origin: "https://evil.example" }, body: "{}"
  }), baseEnv);
  assert.equal(response.status, 403);
});

test("GPU 설명을 구조화해서 반환한다", async () => {
  const env = { ...baseEnv, AI: { run: async () => ({ response: JSON.stringify({
    title: "GPU", english: "Graphics Processing Unit", industry: "common", category: "컴퓨팅",
    definition: "대규모 병렬 연산에 특화된 프로세서다.", principle: "다수의 연산 코어가 데이터를 병렬 처리한다.",
    industry_meaning: "AI 연산 수요와 HBM 수요를 연결한다.", article_meaning: "AI 가속기 문맥에서 사용됐다.",
    related: ["HBM", "AI 가속기"]
  }) }) } };
  const token = await login(env);
  const response = await post({ action: "term_explain", term: "GPU", articleTitle: "GPU 수요 증가", articleContext: "GPU와 HBM 수요가 늘었다." }, env, token);
  assert.equal(response.status, 200);
  const body = await response.json();
  assert.equal(body.term.title, "GPU");
  assert.deepEqual(body.term.related, ["HBM", "AI 가속기"]);
});

test("용어 저장 후 삭제 데이터를 영구 파일 형식으로 쓴다", async () => {
  const originalFetch = globalThis.fetch;
  let stored = { schema_version: 1, updated_at: "", terms: [] };
  globalThis.fetch = async (_url, options = {}) => {
    if (options.method === "PUT") {
      const payload = JSON.parse(options.body);
      stored = JSON.parse(Buffer.from(payload.content, "base64").toString("utf8"));
      return new Response("{}", { status: 200 });
    }
    return new Response(JSON.stringify({ sha: "current", content: Buffer.from(JSON.stringify(stored)).toString("base64") }), { status: 200 });
  };
  try {
    const token = await login(baseEnv);
    const save = await post({ action: "term_save", term: {
      title: "GPU", english: "Graphics Processing Unit", industry: "common", definition: "병렬 연산 프로세서",
      principle: "병렬 처리", industry_meaning: "AI 연산", article_meaning: "기사 의미", related: ["HBM"]
    } }, baseEnv, token);
    assert.equal(save.status, 200);
    const savedBody = await save.json();
    assert.equal(stored.terms.length, 1);
    const remove = await post({ action: "term_delete", termId: savedBody.term.id }, baseEnv, token);
    assert.equal(remove.status, 200);
    assert.equal(stored.terms.length, 0);
  } finally { globalThis.fetch = originalFetch; }
});
