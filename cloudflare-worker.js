/**
 * RiiChain WMS Proxy — Cloudflare Worker 版
 * 直接部署到 Cloudflare Workers，swaner.vip 通过域名相对路径调用
 * 无需任何服务器
 *
 * 部署: 复制到 Cloudflare Dashboard → Workers & Pages → Create → Workers
 */

// ── WMS 配置 ── (部署后在 Worker Settings → Variables 中设置更安全)
const WMS_APP_KEY = "60d2da562ee3492e8bdaaea44c611910";
const WMS_SECRET = "e7f3e07d4f15438da02308fa1ebf90be";
const WMS_BASE_URL = "https://api.xlwms.com";

// ── 签名算法 (HMAC-SHA256) ──
async function makeSign(params, path, secret) {
  const sortedKeys = Object.keys(params).sort();
  const valToString = (v) => {
    if (typeof v === "object" && !Array.isArray(v)) {
      const inner = Object.entries(v)
        .map(([k, iv]) => `${k}=${iv}`)
        .join(",");
      return `{${inner}}`;
    }
    return String(v);
  };
  const step2 = sortedKeys.map((k) => `${k}${valToString(params[k])}`).join("");
  const signStr = secret + path + step2 + secret;

  // Web Crypto API: HMAC-SHA256
  const encoder = new TextEncoder();
  const key = await crypto.subtle.importKey(
    "raw", encoder.encode(secret),
    { name: "HMAC", hash: "SHA-256" }, false, ["sign"]
  );
  const sig = await crypto.subtle.sign("HMAC", key, encoder.encode(signStr));
  return Array.from(new Uint8Array(sig))
    .map((b) => b.toString(16).padStart(2, "0"))
    .join("")
    .toUpperCase();
}

// ── 产品类型分类 ──
function classifyProductType(sku, productName) {
  const nameLower = `${sku} ${productName || ""}`.toLowerCase();
  const accKeywords = [
    "stand", "base", "rotatable", "display", "holder", "frame",
    "mount", "bracket", "hook", "hanger", "pedestal", "chain",
    "22mm", "connector", "screw", "spacer", "ring", "pin",
  ];
  for (const kw of accKeywords) {
    if (nameLower.includes(kw)) return "Accessories";
  }
  return "Customization";
}

// ── 响应格式转换 ──
function transformWaveResponse(wmsData) {
  if (wmsData.code !== 200 && wmsData.code !== "200") return wmsData;
  const raw = wmsData.data || {};
  const orderList = raw.orderList || [];
  const details = [];
  for (const order of orderList) {
    const orderNo = order.outboundOrderNo || "";
    const trackingNo = order.logisticsTrackNo || "";
    const carrier = order.logisticsCarrier || "";
    const sheetUrl = order.sheetUrl || "";
    for (const product of order.productList || []) {
      const sku = product.sku || "";
      const qty = product.quantity || 1;
      const productName = product.productName || "";
      details.push({
        orderNo,
        sku,
        qty,
        productType: classifyProductType(sku, productName),
        trackingNo,
        carrier,
        productName,
        sheetUrl,
      });
    }
  }
  return {
    code: 0,
    data: {
      waveNo: raw.waveNo || "",
      waveStatus: raw.waveStatus,
      sortingStatus: raw.sortingStatus,
      reviewStatus: raw.reviewStatus,
      outboundStatus: raw.outboundStatus,
      details,
    },
  };
}

// ── Worker 入口 ──
export default {
  async fetch(request, env, ctx) {
    const url = new URL(request.url);
    // Strip route prefix when deployed under swaner.vip/api/wms/
    let path = url.pathname;
    if (path.startsWith("/api/wms")) {
      path = path.slice(8); // remove "/api/wms" prefix
    }

    // CORS 预检
    if (request.method === "OPTIONS") {
      return new Response(null, {
        headers: {
          "Access-Control-Allow-Origin": "*",
          "Access-Control-Allow-Methods": "POST, GET, OPTIONS",
          "Access-Control-Allow-Headers": "Content-Type",
        },
      });
    }

    // Health Check
    if (request.method === "GET" && path === "/health") {
      return Response.json({ status: "ok", service: "wms-proxy-cf" }, {
        headers: { "Access-Control-Allow-Origin": "*" },
      });
    }

    // Wave Detail
    if (request.method === "POST" && path === "/wave-detail") {
      try {
        const body = await request.json();
        const waveNo = body.waveNo || "";
        if (!waveNo) {
          return Response.json({ error: "missing waveNo" }, {
            status: 400,
            headers: { "Access-Control-Allow-Origin": "*" },
          });
        }

        const timestamp = String(Math.floor(Date.now() / 1000));
        const apiPath = "/openapi/v2/wave/detail";
        const reqParams = {
          appKey: env.WMS_APP_KEY || WMS_APP_KEY,
          data: { waveNo },
          timestamp,
        };
        const sign = await makeSign(reqParams, apiPath, env.WMS_SECRET || WMS_SECRET);

        const wmsBody = JSON.stringify({
          appKey: env.WMS_APP_KEY || WMS_APP_KEY,
          data: { waveNo },
          timestamp,
          sign,
        });

        const wmsResp = await fetch(`${env.WMS_BASE_URL || WMS_BASE_URL}${apiPath}`, {
          method: "POST",
          headers: {
            "Content-Type": "application/json",
            Accept: "application/json",
          },
          body: wmsBody,
        });

        const wmsData = await wmsResp.json();
        const transformed = transformWaveResponse(wmsData);

        return Response.json(transformed, {
          headers: { "Access-Control-Allow-Origin": "*" },
        });
      } catch (e) {
        return Response.json({ error: e.message }, {
          status: 500,
          headers: { "Access-Control-Allow-Origin": "*" },
        });
      }
    }

    // ── SKU Master API ──
    // GET /sku/list?productType=&dateFrom=&dateTo=&search=
    if (request.method === "GET" && path === "/sku/list") {
      try {
        const params = url.searchParams;
        let productType = params.get("productType") || "";
        let dateFrom = params.get("dateFrom") || "";
        let dateTo = params.get("dateTo") || "";
        let search = (params.get("search") || "").toLowerCase();

        let skus = await loadSkus(env);
        if (productType) skus = skus.filter(s => s.productType === productType);
        if (dateFrom) skus = skus.filter(s => s.maintainDate >= dateFrom);
        if (dateTo) skus = skus.filter(s => s.maintainDate <= dateTo);
        if (search) skus = skus.filter(s => s.sku.toLowerCase().includes(search));

        return Response.json({ data: skus, total: skus.length }, {
          headers: { "Access-Control-Allow-Origin": "*" },
        });
      } catch (e) {
        return Response.json({ error: e.message }, { status: 500, headers: { "Access-Control-Allow-Origin": "*" } });
      }
    }

    // POST /sku/upsert — body: {skus: [{sku, productType, maintainDate?, source?, status?}]}
    if (request.method === "POST" && path === "/sku/upsert") {
      try {
        const body = await request.json();
        const newSkus = body.skus || [];
        if (!newSkus.length) {
          return Response.json({ error: "missing skus array" }, { status: 400, headers: { "Access-Control-Allow-Origin": "*" } });
        }
        const today = new Date().toISOString().slice(0, 10);
        let allSkus = await loadSkus(env);
        for (const item of newSkus) {
          const idx = allSkus.findIndex(s => s.sku === item.sku);
          const record = {
            sku: item.sku,
            productType: item.productType || "Customization",
            maintainDate: item.maintainDate || today,
            source: item.source || "manual",
            status: item.status || "",
          };
          if (idx >= 0) allSkus[idx] = record;
          else allSkus.push(record);
        }
        await saveSkus(env, allSkus);
        return Response.json({ ok: true, count: newSkus.length, total: allSkus.length }, {
          headers: { "Access-Control-Allow-Origin": "*" },
        });
      } catch (e) {
        return Response.json({ error: e.message }, { status: 500, headers: { "Access-Control-Allow-Origin": "*" } });
      }
    }

    // POST /sku/delete — body: {skus: ["SKU001", "SKU002"]}
    if (request.method === "POST" && path === "/sku/delete") {
      try {
        const body = await request.json();
        const delList = body.skus || [];
        if (!delList.length) {
          return Response.json({ error: "missing skus array" }, { status: 400, headers: { "Access-Control-Allow-Origin": "*" } });
        }
        const delSet = new Set(delList);
        let allSkus = await loadSkus(env);
        const before = allSkus.length;
        allSkus = allSkus.filter(s => !delSet.has(s.sku));
        await saveSkus(env, allSkus);
        return Response.json({ ok: true, deleted: before - allSkus.length }, {
          headers: { "Access-Control-Allow-Origin": "*" },
        });
      } catch (e) {
        return Response.json({ error: e.message }, { status: 500, headers: { "Access-Control-Allow-Origin": "*" } });
      }
    }

    // GET /sku/stats
    if (request.method === "GET" && path === "/sku/stats") {
      try {
        let skus = await loadSkus(env);
        const stats = { total: skus.length, standard: 0, accessories: 0, customization: 0, byDate: {} };
        skus.forEach(s => {
          if (s.productType === "Standard") stats.standard++;
          else if (s.productType === "Accessories") stats.accessories++;
          else stats.customization++;
          const d = s.maintainDate;
          if (d) stats.byDate[d] = (stats.byDate[d] || 0) + 1;
        });
        return Response.json({ data: stats }, {
          headers: { "Access-Control-Allow-Origin": "*" },
        });
      } catch (e) {
        return Response.json({ error: e.message }, { status: 500, headers: { "Access-Control-Allow-Origin": "*" } });
      }
    }

    // 404
    return Response.json({ error: "use POST /wave-detail or GET /health" }, {
      status: 404,
      headers: { "Access-Control-Allow-Origin": "*" },
    });
  },
};

// ── KV helpers ──
async function loadSkus(env) {
  try {
    const raw = await env.SKU_MASTER.get("all_skus");
    return raw ? JSON.parse(raw) : [];
  } catch (e) {
    return [];
  }
}

async function saveSkus(env, skus) {
  await env.SKU_MASTER.put("all_skus", JSON.stringify(skus));
}
