#!/bin/bash
# Create GitHub release and upload distribution files

set -e

PYPROJECT_FILE="pyproject.toml"
REPO="PyroMind-Dynamics/pyromind-sdk"

# Parse command line arguments (--version, --delete, --help)
DELETE_RELEASE=false
while [ $# -gt 0 ]; do
    case "$1" in
        --version|-v)
            if [ -n "${2:-}" ]; then
                VERSION="$2"
                shift 2
            else
                echo "Error: --version requires a value (e.g. 0.1.2)"
                exit 1
            fi
            ;;
        --delete|-d)
            DELETE_RELEASE=true
            shift
            ;;
        --help|-h)
            echo "Usage: $0 [--version VERSION | -v VERSION] [--delete | -d]"
            echo "  --version, -v   Set version and update pyproject.toml (e.g. 0.1.2)"
            echo "  --delete, -d   Delete existing release and tag before creating"
            echo "  --help, -h     Show this help"
            echo "Version can also be set via: PYROMIND_VERSION or VERSION"
            exit 0
            ;;
        *)
            echo "Unknown option: $1 (use --help)"
            exit 1
            ;;
    esac
done

VERSION="${VERSION:-${PYROMIND_VERSION:-}}"

if [ -n "$VERSION" ]; then
    if [[ ! "$VERSION" =~ ^[0-9]+\.[0-9]+\.[0-9]+([.-][0-9A-Za-z]+)*$ ]]; then
        echo "Error: invalid version '${VERSION}'"
        echo "Expected format like: 0.1.2 or 0.1.2-rc1"
        exit 1
    fi
    if [ ! -f "$PYPROJECT_FILE" ]; then
        echo "Error: ${PYPROJECT_FILE} not found"
        exit 1
    fi

    echo "Updating ${PYPROJECT_FILE} version to ${VERSION}..."
    python - "$PYPROJECT_FILE" "$VERSION" <<'PY'
import pathlib
import re
import sys

file_path = pathlib.Path(sys.argv[1])
new_version = sys.argv[2]
content = file_path.read_text(encoding="utf-8")
updated, count = re.subn(
    r'(?m)^version\s*=\s*"[^"]*"\s*$',
    f'version = "{new_version}"',
    content,
    count=1,
)
if count != 1:
    raise SystemExit("Error: failed to locate [project] version in pyproject.toml")
file_path.write_text(updated, encoding="utf-8")
PY
fi

if [ -z "$VERSION" ]; then
    if [ ! -f "$PYPROJECT_FILE" ]; then
        echo "Error: ${PYPROJECT_FILE} not found"
        exit 1
    fi
    VERSION=$(python - "$PYPROJECT_FILE" <<'PY'
import pathlib
import re
import sys

content = pathlib.Path(sys.argv[1]).read_text(encoding="utf-8")
match = re.search(r'(?m)^version\s*=\s*"([^"]*)"\s*$', content)
if not match:
    raise SystemExit("Error: failed to read version from pyproject.toml")
print(match.group(1))
PY
)
fi

TAG="v${VERSION}"

# Check if GITHUB_TOKEN is set
if [ -z "$GITHUB_TOKEN" ]; then
    echo "Error: GITHUB_TOKEN environment variable is not set"
    echo "Please set it with: export GITHUB_TOKEN=your_token_here"
    echo ""
    echo "You can create a token at: https://github.com/settings/tokens"
    echo "Required scopes: repo (for private repos) or public_repo (for public repos)"
    exit 1
fi

# Verify token and repository access
echo "Verifying GitHub token and repository access..."

# Try Bearer authentication first (recommended), fallback to token
AUTH_TEST=$(curl -s -w "\n%{http_code}" -H "Authorization: Bearer ${GITHUB_TOKEN}" \
    -H "Accept: application/vnd.github.v3+json" \
    "https://api.github.com/repos/${REPO}")

AUTH_HTTP_CODE=$(echo "$AUTH_TEST" | tail -n1)
AUTH_BODY=$(echo "$AUTH_TEST" | sed '$d')

# If Bearer fails with 401, try token format
if [ "$AUTH_HTTP_CODE" = "401" ]; then
    echo "Trying alternative authentication format..."
    AUTH_TEST=$(curl -s -w "\n%{http_code}" -H "Authorization: token ${GITHUB_TOKEN}" \
        -H "Accept: application/vnd.github.v3+json" \
        "https://api.github.com/repos/${REPO}")
    AUTH_HTTP_CODE=$(echo "$AUTH_TEST" | tail -n1)
    AUTH_BODY=$(echo "$AUTH_TEST" | sed '$d')
    AUTH_HEADER="token"
else
    AUTH_HEADER="Bearer"
fi

if [ "$AUTH_HTTP_CODE" = "401" ]; then
    echo "Error: Invalid GitHub token (401 Unauthorized)"
    echo "Please check your token at: https://github.com/settings/tokens"
    echo "Make sure the token has 'repo' scope for private repos or 'public_repo' for public repos"
    echo ""
    echo "Token format should be:"
    echo "  - Classic PAT: ghp_xxxxxxxxxxxx (starts with ghp_)"
    echo "  - Fine-grained PAT: github_pat_xxxxxxxxxxxx (starts with github_pat_)"
    exit 1
elif [ "$AUTH_HTTP_CODE" = "404" ]; then
    echo "Error: Repository not found (404)"
    echo "Repository: ${REPO}"
    echo ""
    echo "This usually means your fine-grained token doesn't have access to this repository."
    echo ""
    echo "Solutions:"
    echo "1. If using Fine-grained Personal Access Token:"
    echo "   - Go to: https://github.com/settings/tokens"
    echo "   - Edit your token"
    echo "   - Under 'Repository access', select 'Selected repositories'"
    echo "   - Add 'PyroMind-Dynamics/pyromind-sdk' to the list"
    echo "   - Make sure 'Contents' and 'Metadata' permissions are set to 'Read and write'"
    echo ""
    echo "2. Use a Classic Personal Access Token instead:"
    echo "   - Go to: https://github.com/settings/tokens?type=beta"
    echo "   - Click 'Generate new token (classic)'"
    echo "   - Select 'repo' scope"
    echo "   - Use the token (starts with ghp_)"
    echo ""
    echo "3. Manually create release:"
    echo "   - Visit: https://github.com/${REPO}/releases/new"
    echo "   - Select tag: ${TAG}"
    echo "   - Upload files from dist/ directory:"
    ls -1 dist/*.whl dist/*.tar.gz 2>/dev/null | sed 's/^/     - /' || echo "     (no files found)"
    exit 1
elif [ "$AUTH_HTTP_CODE" != "200" ]; then
    echo "Error: Failed to verify repository access"
    echo "HTTP Code: ${AUTH_HTTP_CODE}"
    echo "Response: ${AUTH_BODY}"
    exit 1
fi

echo "✓ Token verified and repository access confirmed (using ${AUTH_HEADER} authentication)"

# Function to delete a release and optionally its tag
delete_release() {
    local tag_name="$1"
    local delete_tag="${2:-false}"
    
    echo "Checking if release ${tag_name} exists..."
    echo "  Repository: ${REPO}"
    echo "  Tag: ${tag_name}"
    
    # Get release info
    RELEASE_INFO=$(curl -s -w "\n%{http_code}" -H "Authorization: ${AUTH_HEADER} ${GITHUB_TOKEN}" \
        -H "Accept: application/vnd.github.v3+json" \
        "https://api.github.com/repos/${REPO}/releases/tags/${tag_name}" 2>&1)
    
    RELEASE_INFO_CODE=$(echo "$RELEASE_INFO" | tail -n1)
    RELEASE_INFO_BODY=$(echo "$RELEASE_INFO" | sed '$d')
    
    # Debug: show HTTP code
    echo "  HTTP Code: ${RELEASE_INFO_CODE}"
    
    if [ "$RELEASE_INFO_CODE" != "200" ]; then
        echo "Error: Release ${tag_name} not found (HTTP Code: ${RELEASE_INFO_CODE})"
        if [ "$RELEASE_INFO_CODE" = "404" ]; then
            echo "  The release may have already been deleted or the tag doesn't exist."
        else
            echo "  Response: ${RELEASE_INFO_BODY}"
        fi
        return 1
    fi
    
    # Try to extract release ID using Python (most reliable)
    if command -v python3 &> /dev/null; then
        RELEASE_ID=$(echo "$RELEASE_INFO_BODY" | python3 -c "import sys, json; print(json.load(sys.stdin)['id'])" 2>/dev/null)
    fi
    
    # Fallback: try with grep
    if [ -z "$RELEASE_ID" ]; then
        RELEASE_ID=$(echo "$RELEASE_INFO_BODY" | grep -o '"id"[[:space:]]*:[[:space:]]*[0-9]*' | head -1 | grep -o '[0-9]*')
    fi
    
    # Another fallback: try with sed
    if [ -z "$RELEASE_ID" ]; then
        RELEASE_ID=$(echo "$RELEASE_INFO_BODY" | sed -n 's/.*"id"[[:space:]]*:[[:space:]]*\([0-9]*\).*/\1/p' | head -1)
    fi
    
    # Last resort: extract first number after "id"
    if [ -z "$RELEASE_ID" ]; then
        RELEASE_ID=$(echo "$RELEASE_INFO_BODY" | grep -o '"id"[^0-9]*[0-9][0-9]*' | grep -o '[0-9][0-9]*' | head -1)
    fi
    
    if [ -z "$RELEASE_ID" ]; then
        echo "Error: Failed to get release ID from response"
        echo "  HTTP Code: ${RELEASE_INFO_CODE}"
        echo "  Response preview:"
        echo "$RELEASE_INFO_BODY" | head -20 | sed 's/^/    /'
        return 1
    fi
    
    echo "Found release with ID: ${RELEASE_ID}"
    echo "Deleting release ${tag_name}..."
    
    # Delete release
    DELETE_RESPONSE=$(curl -s -w "\n%{http_code}" -X DELETE \
        -H "Authorization: ${AUTH_HEADER} ${GITHUB_TOKEN}" \
        -H "Accept: application/vnd.github.v3+json" \
        "https://api.github.com/repos/${REPO}/releases/${RELEASE_ID}")
    
    DELETE_CODE=$(echo "$DELETE_RESPONSE" | tail -n1)
    
    if [ "$DELETE_CODE" = "204" ]; then
        echo "✓ Release deleted successfully"
        
        # Delete tag if requested
        if [ "$delete_tag" = "true" ]; then
            echo "Deleting tag ${tag_name}..."
            TAG_DELETE_RESPONSE=$(curl -s -w "\n%{http_code}" -X DELETE \
                -H "Authorization: ${AUTH_HEADER} ${GITHUB_TOKEN}" \
                -H "Accept: application/vnd.github.v3+json" \
                "https://api.github.com/repos/${REPO}/git/refs/tags/${tag_name}")
            
            TAG_DELETE_CODE=$(echo "$TAG_DELETE_RESPONSE" | tail -n1)
            
            if [ "$TAG_DELETE_CODE" = "204" ]; then
                echo "✓ Tag deleted successfully"
            else
                echo "⚠ Failed to delete tag (HTTP Code: ${TAG_DELETE_CODE})"
                echo "  You may need to delete it manually: git push origin :refs/tags/${tag_name}"
            fi
        fi
        
        return 0
    else
        echo "Error: Failed to delete release (HTTP Code: ${DELETE_CODE})"
        return 1
    fi
}

# Handle delete operation (after token verification)
if [ "$DELETE_RELEASE" = true ]; then
    # Delete release and tag
    if delete_release "$TAG" "true"; then
        echo ""
        echo "Release ${TAG} and its tag have been deleted."
        exit 0
    else
        exit 1
    fi
fi

# Check if dist files exist
if [ ! -d "dist" ]; then
    echo "Error: dist/ directory not found"
    echo "Please run build_and_publish.sh first"
    exit 1
fi

# Check for whl and tar.gz files separately
WHL_COUNT=$(find dist -name "*.whl" -type f 2>/dev/null | wc -l)
TAR_COUNT=$(find dist -name "*.tar.gz" -type f 2>/dev/null | wc -l)

if [ "$WHL_COUNT" -eq 0 ] && [ "$TAR_COUNT" -eq 0 ]; then
    echo "Error: Distribution files not found in dist/ directory"
    echo "Please run build_and_publish.sh first"
    exit 1
fi

if [ "$WHL_COUNT" -gt 0 ]; then
    echo "Found ${WHL_COUNT} wheel file(s)"
fi
if [ "$TAR_COUNT" -gt 0 ]; then
    echo "Found ${TAR_COUNT} source distribution file(s)"
fi

echo "Creating GitHub release for ${TAG}..."

# Create release using GitHub API
RELEASE_RESPONSE=$(curl -s -w "\n%{http_code}" -X POST \
    -H "Authorization: ${AUTH_HEADER} ${GITHUB_TOKEN}" \
    -H "Accept: application/vnd.github.v3+json" \
    "https://api.github.com/repos/${REPO}/releases" \
    -d "{
        \"tag_name\": \"${TAG}\",
        \"name\": \"Release ${TAG}\",
        \"body\": \"## PyroMind SDK ${VERSION}\\n\\n### Installation\\n\\n\`\`\`bash\\npip install pyromind-sdk==${VERSION}\\n\`\`\`\\n\\n### Distribution Files\\n\\n- Source distribution (tar.gz)\\n- Wheel distribution (whl)\\n\",
        \"draft\": false,
        \"prerelease\": false
    }")

HTTP_CODE=$(echo "$RELEASE_RESPONSE" | tail -n1)
RELEASE_BODY=$(echo "$RELEASE_RESPONSE" | sed '$d')

if [ "$HTTP_CODE" != "201" ]; then
    if echo "$RELEASE_BODY" | grep -q "already exists"; then
        echo "Release ${TAG} already exists."
        echo ""
        echo "Options:"
        echo "  1. Delete existing release and recreate:"
        echo "     $0 --delete"
        echo "     $0"
        echo ""
        echo "  2. Or manually delete at: https://github.com/${REPO}/releases/tag/${TAG}"
        echo ""
        echo "Proceeding with existing release (will only upload missing files)..."
        RELEASE_ID=$(curl -s -H "Authorization: ${AUTH_HEADER} ${GITHUB_TOKEN}" \
            -H "Accept: application/vnd.github.v3+json" \
            "https://api.github.com/repos/${REPO}/releases/tags/${TAG}" | \
            grep -o '"id":[0-9]*' | head -1 | cut -d':' -f2)
        
        if [ -z "$RELEASE_ID" ]; then
            echo "Error: Failed to get release ID"
            exit 1
        fi
        echo "Found existing release with ID: ${RELEASE_ID}"
    else
        echo "Error: Failed to create release"
        echo "HTTP Code: ${HTTP_CODE}"
        echo "Response: ${RELEASE_BODY}"
        exit 1
    fi
else
    # Extract release ID
    if command -v python3 &> /dev/null; then
        RELEASE_ID=$(echo "$RELEASE_BODY" | python3 -c "import sys, json; print(json.load(sys.stdin)['id'])" 2>/dev/null)
    fi
    
    # Fallback: try with grep
    if [ -z "$RELEASE_ID" ]; then
        RELEASE_ID=$(echo "$RELEASE_BODY" | grep -o '"id"[[:space:]]*:[[:space:]]*[0-9]*' | head -1 | grep -o '[0-9]*')
    fi
    
    # Another fallback
    if [ -z "$RELEASE_ID" ]; then
        RELEASE_ID=$(echo "$RELEASE_BODY" | grep -o '"id":[0-9]*' | head -1 | cut -d':' -f2)
    fi
    
    if [ -z "$RELEASE_ID" ]; then
        echo "Error: Failed to get release ID from created release"
        echo "  Response: $(echo "$RELEASE_BODY" | head -c 200)"
        exit 1
    fi
    
    echo "Release created successfully with ID: ${RELEASE_ID}"
fi

# Upload distribution files
echo ""
echo "Uploading distribution files..."

# Use find to reliably get all distribution files
UPLOAD_ERRORS=0

# Upload wheel files
while IFS= read -r -d '' file; do
    filename=$(basename "$file")
    echo "Uploading ${filename}..."
    
    UPLOAD_RESPONSE=$(curl -s -w "\n%{http_code}" -X POST \
        -H "Authorization: ${AUTH_HEADER} ${GITHUB_TOKEN}" \
        -H "Accept: application/vnd.github.v3+json" \
        -H "Content-Type: application/octet-stream" \
        --data-binary "@${file}" \
        "https://uploads.github.com/repos/${REPO}/releases/${RELEASE_ID}/assets?name=${filename}")
    
    UPLOAD_HTTP_CODE=$(echo "$UPLOAD_RESPONSE" | tail -n1)
    UPLOAD_BODY=$(echo "$UPLOAD_RESPONSE" | sed '$d')
    
    if [ "$UPLOAD_HTTP_CODE" = "201" ]; then
        echo "  ✓ ${filename} uploaded successfully"
    else
        if echo "$UPLOAD_BODY" | grep -qi "already_exists\|already exists\|duplicate"; then
            echo "  ⚠ ${filename} already exists in release (skipping)"
        else
            echo "  ✗ Failed to upload ${filename}"
            echo "    HTTP Code: ${UPLOAD_HTTP_CODE}"
            echo "    Response: ${UPLOAD_BODY}"
            UPLOAD_ERRORS=$((UPLOAD_ERRORS + 1))
        fi
    fi
done < <(find dist -name "*.whl" -type f -print0)

# Upload source distribution files
while IFS= read -r -d '' file; do
    filename=$(basename "$file")
    echo "Uploading ${filename}..."
    
    UPLOAD_RESPONSE=$(curl -s -w "\n%{http_code}" -X POST \
        -H "Authorization: ${AUTH_HEADER} ${GITHUB_TOKEN}" \
        -H "Accept: application/vnd.github.v3+json" \
        -H "Content-Type: application/octet-stream" \
        --data-binary "@${file}" \
        "https://uploads.github.com/repos/${REPO}/releases/${RELEASE_ID}/assets?name=${filename}")
    
    UPLOAD_HTTP_CODE=$(echo "$UPLOAD_RESPONSE" | tail -n1)
    UPLOAD_BODY=$(echo "$UPLOAD_RESPONSE" | sed '$d')
    
    if [ "$UPLOAD_HTTP_CODE" = "201" ]; then
        echo "  ✓ ${filename} uploaded successfully"
    else
        if echo "$UPLOAD_BODY" | grep -qi "already_exists\|already exists\|duplicate"; then
            echo "  ⚠ ${filename} already exists in release (skipping)"
        else
            echo "  ✗ Failed to upload ${filename}"
            echo "    HTTP Code: ${UPLOAD_HTTP_CODE}"
            echo "    Response: ${UPLOAD_BODY}"
            UPLOAD_ERRORS=$((UPLOAD_ERRORS + 1))
        fi
    fi
done < <(find dist -name "*.tar.gz" -type f -print0)

if [ "$UPLOAD_ERRORS" -gt 0 ]; then
    echo ""
    echo "Warning: ${UPLOAD_ERRORS} file(s) failed to upload"
    exit 1
fi

echo ""
echo "Release ${TAG} is ready!"
echo "View it at: https://github.com/${REPO}/releases/tag/${TAG}"

