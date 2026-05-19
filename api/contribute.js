/**
 * POST /api/contribute
 *
 * Receives UUID contributions from BG3MM_UpdateHelper app.
 * Saves each contribution batch as an individual file under contributions/
 * to avoid write conflicts.
 *
 * IP-based rate limit: 1 contribution per IP per 24 hours.
 * IP is stored as SHA-256 hash (privacy safe) in ip_logs/ folder.
 *
 * Body: [{ pakFileName, uuid, modId?, fileId? }, ...]
 */

import crypto from "crypto";

const REPO_OWNER = "Eolaloe";
const REPO_NAME  = "bg3-nexus-uuid-db";
const BRANCH     = "main";
const GITHUB_API = "https://api.github.com";

const UUID_REGEX = /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i;
const RATE_LIMIT_MS = 24 * 60 * 60 * 1000; // 24 hours

export const config = {
    api: {
        bodyParser: {
            sizeLimit: '1mb',
        },
    },
};

// ── GitHub helpers ────────────────────────────────────────────────────────────

async function githubGet(path, token) {
    const resp = await fetch(`${GITHUB_API}/repos/${REPO_OWNER}/${REPO_NAME}/contents/${path}?ref=${BRANCH}`, {
        headers: {
            "Authorization": `Bearer ${token}`,
            "Accept":        "application/vnd.github+json",
            "User-Agent":    "bg3-nexus-uuid-db-contribute/1.0",
        },
    });
    return resp;
}

async function githubPut(path, content, message, token, sha = null) {
    const body = { message, content, branch: BRANCH };
    if (sha) body.sha = sha;

    return await fetch(`${GITHUB_API}/repos/${REPO_OWNER}/${REPO_NAME}/contents/${path}`, {
        method: "PUT",
        headers: {
            "Authorization": `Bearer ${token}`,
            "Accept":        "application/vnd.github+json",
            "User-Agent":    "bg3-nexus-uuid-db-contribute/1.0",
            "Content-Type":  "application/json",
        },
        body: JSON.stringify(body),
    });
}

// ── IP rate limit ─────────────────────────────────────────────────────────────

async function checkAndRecordIp(ipHash, token) {
    // List ip_logs/ folder to find existing file for this IP
    const listResp = await fetch(
        `${GITHUB_API}/repos/${REPO_OWNER}/${REPO_NAME}/contents/ip_logs?ref=${BRANCH}`,
        {
            headers: {
                "Authorization": `Bearer ${token}`,
                "Accept":        "application/vnd.github+json",
                "User-Agent":    "bg3-nexus-uuid-db-contribute/1.0",
            },
        }
    );

    if (listResp.ok) {
        const files = await listResp.json();
        const now   = Date.now();

        for (const file of files) {
            // filename format: {ipHash}_{timestamp}.json
            const parts = file.name.replace(".json", "").split("_");
            if (parts[0] === ipHash) {
                const ts = parseInt(parts[1]);
                if (!isNaN(ts) && now - ts < RATE_LIMIT_MS) {
                    return false; // rate limited
                }
            }
        }
    }

    // Record this IP
    const now       = Date.now();
    const fileName  = `ip_logs/${ipHash}_${now}.json`;
    const content   = Buffer.from("{}").toString("base64");

    await githubPut(fileName, content, `IP log: ${ipHash.substring(0, 8)}`, token);
    return true; // allowed
}

// ── Main handler ──────────────────────────────────────────────────────────────

export default async function handler(req, res) {
    if (req.method !== "POST") {
        return res.status(405).json({ error: "Method not allowed" });
    }

    const token = process.env.GITHUB_TOKEN;
    if (!token) {
        return res.status(500).json({ error: "Server misconfigured" });
    }

    // ── IP hash ───────────────────────────────────────────────────────────
    const rawIp  = req.headers["x-forwarded-for"]?.split(",")[0]?.trim() || "unknown";
    const ipHash = crypto.createHash("sha256").update(rawIp).digest("hex").substring(0, 16);

    // ── Rate limit check ──────────────────────────────────────────────────
    const allowed = await checkAndRecordIp(ipHash, token);
    if (!allowed) {
        return res.status(429).json({ error: "Rate limited: 1 contribution per 24 hours" });
    }

    // ── Validate body ─────────────────────────────────────────────────────
    let items = req.body;
    if (!Array.isArray(items)) items = [items];

    const valid = items.filter(item =>
        item &&
        typeof item.pakFileName === "string" &&
        item.pakFileName.toLowerCase().endsWith(".pak") &&
        typeof item.metaUuid === "string" &&
        UUID_REGEX.test(item.metaUuid)
    ).map(item => ({
        pakFileName:  item.pakFileName,
        metaUuid:     item.metaUuid,
        nexusModId:   item.nexusModId  || null,
        nexusFileId:  item.nexusFileId || null,
    }));

    if (valid.length === 0) {
        return res.status(400).json({ error: "No valid contributions" });
    }

    // ── Save contribution file ────────────────────────────────────────────
    const now      = new Date();
    const timestamp = now.toISOString().replace(/[-:T]/g, "").replace(/\..+/, "");
    const micro    = Math.floor(Math.random() * 1000000).toString().padStart(6, "0");
    const fileName = `contributions/${timestamp}_${micro}.json`;
    const content  = Buffer.from(JSON.stringify(valid, null, 2)).toString("base64");

    try {
        const resp = await githubPut(fileName, content, `Contribute: ${valid.length} UUID(s)`, token);
        if (!resp.ok) {
            const err = await resp.json();
            return res.status(500).json({ error: "Failed to save", detail: err.message });
        }
    } catch {
        return res.status(500).json({ error: "Failed to save contribution" });
    }

    return res.status(200).json({
        status: "received",
        count:  valid.length,
        file:   fileName,
    });
}
