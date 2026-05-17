/**
 * POST /api/contribute
 *
 * Receives UUID contributions from BG3MM_UpdateHelper app.
 * Saves each contribution batch as an individual file under contributions/
 * to avoid write conflicts.
 *
 * Body: [{ pakFileName, uuid, modId?, fileId? }, ...]
 */

const REPO_OWNER = "Eolaloe";
const REPO_NAME  = "bg3-nexus-uuid-db";
const BRANCH     = "main";
const GITHUB_API = "https://api.github.com";

const UUID_REGEX = /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i;

export default async function handler(req, res) {
    if (req.method !== "POST") {
        return res.status(405).json({ error: "Method not allowed" });
    }

    const token = process.env.GITHUB_TOKEN;
    if (!token) {
        return res.status(500).json({ error: "Server misconfigured" });
    }

    // Accept both single object and array
    let items = req.body;
    if (!Array.isArray(items)) items = [items];

    // ── Validate ──────────────────────────────────────────────────────────
    const valid = items.filter(item =>
        item &&
        typeof item.pakFileName === "string" &&
        item.pakFileName.toLowerCase().endsWith(".pak") &&
        typeof item.uuid === "string" &&
        UUID_REGEX.test(item.uuid)
    ).map(item => ({
        pakFileName: item.pakFileName,
        uuid:        item.uuid,
        modId:       item.modId  || null,
        fileId:      item.fileId || null,
    }));

    if (valid.length === 0) {
        return res.status(400).json({ error: "No valid contributions" });
    }

    // ── Generate unique filename ──────────────────────────────────────────
    const now       = new Date();
    const timestamp = now.toISOString().replace(/[-:T]/g, "").replace(/\..+/, "");
    const micro     = Math.floor(Math.random() * 1000000).toString().padStart(6, "0");
    const fileName  = `contributions/${timestamp}_${micro}.json`;

    // ── Save to GitHub ────────────────────────────────────────────────────
    const content = Buffer.from(JSON.stringify(valid, null, 2)).toString("base64");

    const headers = {
        "Authorization": `Bearer ${token}`,
        "Accept":        "application/vnd.github+json",
        "User-Agent":    "bg3-nexus-uuid-db-contribute/1.0",
        "Content-Type":  "application/json",
    };

    try {
        const resp = await fetch(
            `${GITHUB_API}/repos/${REPO_OWNER}/${REPO_NAME}/contents/${fileName}`,
            {
                method: "PUT",
                headers,
                body: JSON.stringify({
                    message: `Contribute: ${valid.length} UUID(s)`,
                    content,
                    branch: BRANCH,
                }),
            });

        if (!resp.ok) {
            const err = await resp.json();
            return res.status(500).json({ error: "Failed to save", detail: err.message });
        }
    } catch {
        return res.status(500).json({ error: "Failed to save contribution" });
    }

    return res.status(200).json({
        status:  "received",
        count:   valid.length,
        file:    fileName,
    });
}
