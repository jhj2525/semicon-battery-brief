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

    const requiredGitHubSettings = [env.GITHUB_OWNER, env.GITHUB_REPO, env.GITHUB_TOKEN];
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
