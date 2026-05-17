/**
 * POST /api/contribute
 * 
 * Receives UUID contributions from BG3MM_UpdateHelper app.
 * Validates input, then appends to contributions.json via GitHub API.
 * 
 * Body: { pakFileName, uuid, modId?, fileId? }
 */

const REPO_OWNER = "Eolaloe";
const REPO_NAME  = "bg3-nexus-uuid-db";
const FILE_PATH  = "contributions.json";
const BRANCH     = "main";

const GITHUB_API = "https://api.github.com";

// UUID format: xxxxxxxx-xxxx-xxxx-xxxx-xxxxxxxxxxxx
const UUID_REGEX = /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i;

export default async function handler(req, res) {
    if (req.method !== "POST") {
        return res.status(405).json({ error: "Method not allowed" });
    }

    const { pakFileName, uuid, modId, fileId } = req.body || {};

    // ── Validation ────────────────────────────────────────────────────────
    if (!pakFileName || typeof pakFileName !== "string" ||
        !pakFileName.toLowerCase().endsWith(".pak")) {
        return res.status(400).json({ error: "Invalid pakFileName" });
    }

    if (!uuid || !UUID_REGEX.test(uuid)) {
        return res.status(400).json({ error: "Invalid UUID format" });
    }

    const token = process.env.GITHUB_TOKEN;
    if (!token) {
        return res.status(500).json({ error: "Server misconfigured" });
    }

    // ── Load current contributions.json ───────────────────────────────────
    const headers = {
        "Authorization": `Bearer ${token}`,
        "Accept":        "application/vnd.github+json",
        "User-Agent":    "bg3-nexus-uuid-db-contribute/1.0",
    };

    let contributions = {};
    let sha = null;

    try {
        const getResp = await fetch(
            `${GITHUB_API}/repos/${REPO_OWNER}/${REPO_NAME}/contents/${FILE_PATH}?ref=${BRANCH}`,
            { headers });

        if (getResp.ok) {
            const data = await getResp.json();
            sha = data.sha;
            contributions = JSON.parse(Buffer.from(data.content, "base64").toString("utf-8"));
        } else if (getResp.status !== 404) {
            return res.status(500).json({ error: "Failed to read contributions" });
        }
    } catch {
        return res.status(500).json({ error: "Failed to read contributions" });
    }

    // ── Update vote count ─────────────────────────────────────────────────
    const key = pakFileName.toLowerCase();
    if (!contributions[key]) {
        contributions[key] = { votes: {}, confirmed: null };
    }

    const entry = contributions[key];

    // Skip if already confirmed with same UUID
    if (entry.confirmed === uuid) {
        return res.status(200).json({ status: "already_confirmed" });
    }

    entry.votes[uuid] = (entry.votes[uuid] || 0) + 1;

    // Majority rule: confirmed when a UUID has > 50% of total votes and >= 3 total
    const total     = Object.values(entry.votes).reduce((a, b) => a + b, 0);
    const maxVotes  = Math.max(...Object.values(entry.votes));
    const maxUuid   = Object.keys(entry.votes).find(k => entry.votes[k] === maxVotes);

    if (total >= 3 && maxVotes > total / 2) {
        entry.confirmed = maxUuid;
    }

    // Store optional metadata
    if (modId)  entry.modId  = modId;
    if (fileId) entry.fileId = fileId;

    // ── Save back to GitHub ───────────────────────────────────────────────
    const content = Buffer.from(JSON.stringify(contributions, null, 2)).toString("base64");
    const message = `Contribute: ${pakFileName} uuid vote`;

    const putBody = { message, content, branch: BRANCH };
    if (sha) putBody.sha = sha;

    try {
        const putResp = await fetch(
            `${GITHUB_API}/repos/${REPO_OWNER}/${REPO_NAME}/contents/${FILE_PATH}`,
            {
                method:  "PUT",
                headers: { ...headers, "Content-Type": "application/json" },
                body:    JSON.stringify(putBody),
            });

        if (!putResp.ok) {
            const err = await putResp.json();
            return res.status(500).json({ error: "Failed to save", detail: err.message });
        }
    } catch {
        return res.status(500).json({ error: "Failed to save contributions" });
    }

    return res.status(200).json({
        status:    entry.confirmed ? "confirmed" : "voted",
        confirmed: entry.confirmed,
        votes:     entry.votes,
    });
}
