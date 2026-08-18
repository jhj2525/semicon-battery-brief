const TOKEN_LIFETIME_SECONDS = 8 * 60 * 60;

const corsHeaders = origin => ({
  "Access-Control-Allow-Origin": origin,
  "Access-Control-Allow-Methods": "POST, OPTIONS",
  "Access-Control-Allow-Headers": "Content-Type, Authorization",
  "Vary": "Origin",
});

const json = (body, status, origin) => new Response(JSON.stringify(body), {
  status,
  headers: {
    "Content-Type": "application/json; charset=utf-8",
    ...corsHeaders(origin),
  },
});

const textEncoder = new TextEncoder();

function base64Url(bytes) {
  let binary = "";
  for (const byte of bytes) binary += String.fromCharCode(byte);
  return btoa(binary).replace(/\+/g, "-").replace(/\//g, "_").replace(/=+$/, "");
}

function decodeBase64Url(value) {
  const normalized = value.replace(/-/g, "+").replace(/_/g, "/");
  const padded = normalized + "=".repeat((4 - normalized.length % 4) % 4);
  const binary = atob(padded);
  return Uint8Array.from(binary, character => character.charCodeAt(0));
}

async function hmacKey(secret) {
  return crypto.subtle.importKey(
    "raw",
    textEncoder.encode(secret),
    { name: "HMAC", hash: "SHA-256" },
    false,
    ["sign", "verify"]
  );
}

async function issueToken(secret) {
  const payload = base64Url(textEncoder.encode(JSON.stringify({
    exp: Math.floor(Date.now() / 1000) + TOKEN_LIFETIME_SECONDS,
    v: 1,
  })));
  const signature = await crypto.subtle.sign(
    "HMAC",
    await hmacKey(secret),
    textEncoder.encode(payload)
  );
  return `${payload}.${base64Url(new Uint8Array(signature))}`;
}

async function verifyToken(token, secret) {
  try {
    const [payload, signature, extra] = String(token || "").split(".");
    if (!payload || !signature || extra) return false;
    const valid = await crypto.subtle.verify(
      "HMAC",
      await hmacKey(secret),
      decodeBase64Url(signature),
      textEncoder.encode(payload)
    );
    if (!valid) return false;
    const claims = JSON.parse(new TextDecoder().decode(decodeBase64Url(payload)));
    return claims.v === 1 && Number.isFinite(claims.exp)
      && claims.exp > Math.floor(Date.now() / 1000);
  } catch {
    return false;
  }
}

function bearerToken(request) {
  const authorization = request.headers.get("Authorization") || "";
  return authorization.startsWith("Bearer ") ? authorization.slice(7).trim() : "";
}

function decodeUtf8Base64(value) {
  const binary = atob(String(value || "").replace(/\n/g, ""));
  return new TextDecoder().decode(Uint8Array.from(binary, character => character.charCodeAt(0)));
}

function encodeUtf8Base64(value) {
  return base64Url(textEncoder.encode(value)).replace(/-/g, "+").replace(/_/g, "/")
    + "=".repeat((4 - base64Url(textEncoder.encode(value)).length % 4) % 4);
}

function githubHeaders(env) {
  return {
    "Accept": "application/vnd.github+json",
    "Authorization": `Bearer ${env.GITHUB_TOKEN}`,
    "X-GitHub-Api-Version": "2022-11-28",
    "User-Agent": "process-brief-admin",
  };
}

function githubContentsUrl(env) {
  return `https://api.github.com/repos/${env.GITHUB_OWNER}/${env.GITHUB_REPO}/contents/data/terms.json`;
}

async function readTerms(env) {
  const response = await fetch(`${githubContentsUrl(env)}?ref=${encodeURIComponent(env.GITHUB_BRANCH || "main")}`, {
    headers: githubHeaders(env),
  });
  if (response.status === 404) return { sha: null, data: { schema_version: 1, updated_at: "", terms: [] } };
  if (!response.ok) throw new Error(`용어 노트 읽기 실패 (${response.status})`);
  const file = await response.json();
  const data = JSON.parse(decodeUtf8Base64(file.content));
  return { sha: file.sha, data: { schema_version: 1, updated_at: data.updated_at || "", terms: Array.isArray(data.terms) ? data.terms : [] } };
}

async function writeTerms(env, data, sha, message) {
  const response = await fetch(githubContentsUrl(env), {
    method: "PUT",
    headers: { ...githubHeaders(env), "Content-Type": "application/json" },
    body: JSON.stringify({
      message,
      branch: env.GITHUB_BRANCH || "main",
      content: encodeUtf8Base64(JSON.stringify(data, null, 2) + "\n"),
      ...(sha ? { sha } : {}),
    }),
  });
  if (!response.ok) {
    const detail = await response.json().catch(() => ({}));
    throw new Error(`용어 노트 저장 실패 (${response.status}): ${detail.message || "원인 미확인"}`);
  }
}

function cleanText(value, maxLength = 4000) {
  return String(value || "").trim().slice(0, maxLength);
}

function normalizeTerm(value) {
  const industry = ["semiconductor", "battery", "common"].includes(value.industry)
    ? value.industry : "common";
  return {
    id: cleanText(value.id, 80),
    title: cleanText(value.title, 80),
    english: cleanText(value.english, 160),
    industry,
    category: cleanText(value.category || "산업 용어", 80),
    definition: cleanText(value.definition, 600),
    principle: cleanText(value.principle, 2400),
    industry_meaning: cleanText(value.industry_meaning, 2400),
    article_meaning: cleanText(value.article_meaning, 2400),
    related: Array.isArray(value.related) ? value.related.map(item => cleanText(item, 80)).filter(Boolean).slice(0, 10) : [],
    article_title: cleanText(value.article_title, 300),
    article_url: cleanText(value.article_url, 1000),
    structure: cleanText(value.structure, 1200),
    process: cleanText(value.process, 1200),
    defect: cleanText(value.defect, 1200),
    job: cleanText(value.job, 1200),
    source: cleanText(value.source || "Process Brief AI 용어 노트", 300),
    created_at: cleanText(value.created_at, 40),
    updated_at: cleanText(value.updated_at, 40),
  };
}

async function explainTerm(env, body) {
  if (!env.AI) throw new Error("Worker의 AI 바인딩이 설정되지 않았습니다.");
  const term = cleanText(body.term, 80);
  const articleTitle = cleanText(body.articleTitle, 300);
  const articleUrl = cleanText(body.articleUrl, 1000);
  const articleContext = cleanText(body.articleContext, 6000);
  if (!term) throw new Error("검색할 용어를 입력해 주세요.");
  const prompt = `다음 산업 용어를 취업 준비생이 정확히 이해하도록 한국어로 설명하라. 추측성 수치나 출처를 만들지 말고, 약어라면 영문 원어를 적어라. 산업 분류는 semiconductor, battery, common 중 하나만 사용하라. 기사 문맥이 없으면 article_meaning에 '기사 문맥이 제공되지 않아 일반적인 의미로 설명했습니다.'라고 적어라. JSON 필드는 title, english, industry, category, definition, principle, industry_meaning, article_meaning, related(문자열 배열), structure, process, defect, job만 사용하라.\n\n용어: ${term}\n기사 제목: ${articleTitle || "없음"}\n기사 문맥:\n${articleContext || "없음"}`;
  const aiResult = await env.AI.run("@cf/zai-org/glm-4.7-flash", {
    messages: [
      { role: "system", content: "반도체·배터리 산업 기술 용어를 검증 가능한 범위에서 설명하는 한국어 튜터다. 반드시 JSON 객체만 출력한다." },
      { role: "user", content: prompt },
    ],
    response_format: { type: "json_object" },
    max_tokens: 1800,
    temperature: 0.2,
  });
  const raw = typeof aiResult === "string" ? aiResult : aiResult.response || aiResult.result || "";
  const parsed = typeof raw === "object" ? raw : JSON.parse(String(raw).replace(/^```json\s*|\s*```$/g, ""));
  const now = new Date().toISOString();
  const normalized = normalizeTerm({
    ...parsed,
    title: parsed.title || term,
    article_title: articleTitle,
    article_url: articleUrl,
    source: articleUrl || "Process Brief AI 용어 노트",
    created_at: now,
    updated_at: now,
  });
  if (!normalized.title || !normalized.definition || !normalized.principle || !normalized.industry_meaning) {
    throw new Error("AI 설명 형식이 완전하지 않습니다. 다시 검색해 주세요.");
  }
  return normalized;
}

export default {
  async fetch(request, env) {
    const origin = request.headers.get("Origin") || "";
    const allowedOrigin = (env.ALLOWED_ORIGIN || "").replace(/\/$/, "");

    if (!allowedOrigin || origin.replace(/\/$/, "") !== allowedOrigin) {
      return json({ error: "허용되지 않은 사이트입니다." }, 403, allowedOrigin || "null");
    }

    if (request.method === "OPTIONS") {
      return new Response(null, { status: 204, headers: corsHeaders(allowedOrigin) });
    }
    if (request.method !== "POST") {
      return json({ error: "POST 요청만 허용됩니다." }, 405, allowedOrigin);
    }

    let body;
    try {
      body = await request.json();
    } catch {
      return json({ error: "입력 형식이 올바르지 않습니다." }, 400, allowedOrigin);
    }

    if (!env.ADMIN_PASSWORD || !env.ADMIN_TOKEN_SECRET) {
      return json({ error: "Worker 관리 인증 설정이 완료되지 않았습니다." }, 500, allowedOrigin);
    }

    const action = body.action || "";
    if (action === "login") {
      if (body.password !== env.ADMIN_PASSWORD) {
        return json({ error: "관리 비밀번호가 올바르지 않습니다." }, 401, allowedOrigin);
      }
      return json({
        ok: true,
        token: await issueToken(env.ADMIN_TOKEN_SECRET),
        expiresIn: TOKEN_LIFETIME_SECONDS,
      }, 200, allowedOrigin);
    }

    if (!await verifyToken(bearerToken(request), env.ADMIN_TOKEN_SECRET)) {
      return json({ error: "관리 인증이 만료되었거나 올바르지 않습니다." }, 401, allowedOrigin);
    }

    const requiredGitHubSettings = [env.GITHUB_OWNER, env.GITHUB_REPO, env.GITHUB_TOKEN];
    if (["term_save", "term_delete"].includes(action) && requiredGitHubSettings.some(value => !value)) {
      return json({ error: "Worker의 GitHub 연결 설정이 완료되지 않았습니다." }, 500, allowedOrigin);
    }

    if (action === "term_explain") {
      try {
        return json({ ok: true, term: await explainTerm(env, body) }, 200, allowedOrigin);
      } catch (error) {
        return json({ error: error.message || "AI 용어 설명 생성에 실패했습니다." }, 502, allowedOrigin);
      }
    }

    if (action === "term_save") {
      try {
        const incoming = normalizeTerm(body.term || {});
        if (!incoming.title || !incoming.definition) return json({ error: "저장할 용어 설명이 올바르지 않습니다." }, 400, allowedOrigin);
        const stored = await readTerms(env);
        const now = new Date().toISOString();
        const key = `${incoming.title}|${incoming.english}`.toLowerCase();
        const existing = stored.data.terms.find(term => `${term.title}|${term.english}`.toLowerCase() === key);
        const saved = { ...incoming, id: existing?.id || crypto.randomUUID(), created_at: existing?.created_at || now, updated_at: now };
        stored.data.terms = [saved, ...stored.data.terms.filter(term => term.id !== existing?.id)];
        stored.data.updated_at = now;
        await writeTerms(env, stored.data, stored.sha, `feat: save term ${saved.title}`);
        return json({ ok: true, term: saved, terms: stored.data.terms }, 200, allowedOrigin);
      } catch (error) {
        return json({ error: error.message || "용어 저장에 실패했습니다." }, 502, allowedOrigin);
      }
    }

    if (action === "term_delete") {
      try {
        const termId = cleanText(body.termId, 80);
        if (!/^[a-zA-Z0-9-]{12,80}$/.test(termId)) return json({ error: "삭제할 용어 ID가 올바르지 않습니다." }, 400, allowedOrigin);
        const stored = await readTerms(env);
        const next = stored.data.terms.filter(term => term.id !== termId);
        if (next.length === stored.data.terms.length) return json({ error: "삭제할 용어를 찾지 못했습니다." }, 404, allowedOrigin);
        stored.data.terms = next;
        stored.data.updated_at = new Date().toISOString();
        await writeTerms(env, stored.data, stored.sha, "chore: delete saved term");
        return json({ ok: true, terms: stored.data.terms }, 200, allowedOrigin);
      } catch (error) {
        return json({ error: error.message || "용어 삭제에 실패했습니다." }, 502, allowedOrigin);
      }
    }

    const inputs = {};
    if (action === "delete") {
      const itemIds = Array.isArray(body.itemIds) ? body.itemIds : [body.itemId];
      const uniqueIds = [...new Set(itemIds.filter(Boolean))];
      if (
        uniqueIds.length < 1 || uniqueIds.length > 100
        || uniqueIds.some(value => !/^[a-f0-9]{12}$/i.test(value))
      ) {
        return json({ error: "삭제할 기사 ID가 올바르지 않습니다." }, 400, allowedOrigin);
      }
      inputs.delete_article_id = uniqueIds.join("\n");
    } else if (action === "edit_title") {
      const title = String(body.title || "").trim();
      if (!/^[a-f0-9]{12}$/i.test(body.itemId || "") || !title || title.length > 300) {
        return json({ error: "수정할 기사와 제목을 확인해 주세요." }, 400, allowedOrigin);
      }
      inputs.edit_article_id = body.itemId;
      inputs.edit_article_title = title;
    } else if (action === "favorite") {
      if (!/^[a-f0-9]{12}$/i.test(body.itemId || "") || typeof body.favorite !== "boolean") {
        return json({ error: "즐겨찾기 요청이 올바르지 않습니다." }, 400, allowedOrigin);
      }
      inputs.favorite_article_id = body.itemId;
      inputs.favorite_state = body.favorite ? "true" : "false";
    } else if (action === "summarize_text") {
      const articleText = String(body.articleText || "").trim();
      if (!/^[a-f0-9]{12}$/i.test(body.itemId || "")) {
        return json({ error: "요약할 기사 ID가 올바르지 않습니다." }, 400, allowedOrigin);
      }
      if (articleText.length < 200 || articleText.length > 30000) {
        return json({ error: "기사 본문은 200~30,000자로 입력해 주세요." }, 400, allowedOrigin);
      }
      inputs.pasted_article_id = body.itemId;
      inputs.pasted_article_text = articleText;
    } else if (action === "add") {
      if (!["semiconductor", "battery", "semi_market"].includes(body.sector)) {
        return json({ error: "분야를 다시 선택해 주세요." }, 400, allowedOrigin);
      }
      if (!Array.isArray(body.urls) || body.urls.length < 1 || body.urls.length > 20) {
        return json({ error: "기사 URL을 1~20개 입력해 주세요." }, 400, allowedOrigin);
      }
      const articleUrls = [];
      for (const value of body.urls) {
        try {
          const articleUrl = new URL(value);
          if (!["http:", "https:"].includes(articleUrl.protocol)) throw new Error();
          articleUrls.push(articleUrl.toString());
        } catch {
          return json({ error: "올바르지 않은 기사 URL이 포함되어 있습니다." }, 400, allowedOrigin);
        }
      }
      inputs.article_url = [...new Set(articleUrls)].join("\n");
      inputs.article_sector = body.sector;
    } else {
      return json({ error: "지원하지 않는 요청입니다." }, 400, allowedOrigin);
    }

    if (requiredGitHubSettings.some(value => !value)) {
      return json({ error: "Worker의 GitHub 연결 설정이 완료되지 않았습니다." }, 500, allowedOrigin);
    }

    const apiUrl = `https://api.github.com/repos/${env.GITHUB_OWNER}/${env.GITHUB_REPO}`
      + "/actions/workflows/update-news.yml/dispatches";
    const githubResponse = await fetch(apiUrl, {
      method: "POST",
      headers: {
        "Accept": "application/vnd.github+json",
        "Authorization": `Bearer ${env.GITHUB_TOKEN}`,
        "X-GitHub-Api-Version": "2022-11-28",
        "User-Agent": "process-brief-admin",
      },
      body: JSON.stringify({ ref: env.GITHUB_BRANCH || "main", inputs }),
    });

    if (!githubResponse.ok) {
      const detail = await githubResponse.json().catch(() => ({}));
      return json({
        error: `GitHub 실행 실패 (${githubResponse.status}): ${detail.message || "원인 미확인"}`
      }, 502, allowedOrigin);
    }
    return json({ ok: true, status: "workflow_dispatched" }, 202, allowedOrigin);
  },
};
