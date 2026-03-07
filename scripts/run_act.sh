#!/usr/bin/env bash
# Run the GitHub Actions CI workflow locally using `act`.
# Each step is validated with exit-code checks and timing.
set -euo pipefail

RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
DIM='\033[2m'
RESET='\033[0m'

REPO_ROOT="$(cd "$(dirname "$0")/.." && pwd)"
PYTHON_VERSION="${1:-3.12}"
IMAGE="${ACT_IMAGE:-catthehacker/ubuntu:act-latest}"
ARCH="${ACT_ARCH:-linux/amd64}"

# Resolve GitHub token
if [ -z "${GITHUB_TOKEN:-}" ]; then
    GITHUB_TOKEN=$(security find-generic-password -a GITHUB_TOKEN -s api-keys -w 2>/dev/null || true)
fi
if [ -z "${GITHUB_TOKEN:-}" ]; then
    echo -e "${RED}✗ GITHUB_TOKEN not found. Set it or add to macOS Keychain.${RESET}"
    exit 1
fi

passed=0
failed=0
skipped=0
results=()

run_step() {
    local name="$1"
    shift
    echo -e "\n${YELLOW}━━━ ${name} ━━━${RESET}"
    local start=$SECONDS
    if "$@"; then
        local dur=$(( SECONDS - start ))
        echo -e "${GREEN}✓ ${name}${RESET} ${DIM}(${dur}s)${RESET}"
        results+=("${GREEN}✓${RESET} ${name} (${dur}s)")
        ((passed++))
    else
        local dur=$(( SECONDS - start ))
        echo -e "${RED}✗ ${name}${RESET} ${DIM}(${dur}s)${RESET}"
        results+=("${RED}✗${RESET} ${name} (${dur}s)")
        ((failed++))
    fi
}

echo -e "${YELLOW}Running act CI for Python ${PYTHON_VERSION}${RESET}"
echo -e "${DIM}Repo: ${REPO_ROOT}${RESET}"
echo -e "${DIM}Image: ${IMAGE}${RESET}\n"

# Step 1: Validate prerequisites
run_step "Check act installed" command -v act

run_step "Check Docker running" docker info --format '{{.ServerVersion}}'

# Step 2: Run the full CI workflow via act
run_step "act lint-and-test (Python ${PYTHON_VERSION})" \
    act -j lint-and-test \
    --matrix "python-version:${PYTHON_VERSION}" \
    -P "ubuntu-latest=${IMAGE}" \
    --container-architecture "${ARCH}" \
    -s "GITHUB_TOKEN=${GITHUB_TOKEN}" \
    -W "${REPO_ROOT}/.github/workflows/ci.yml" \
    --directory "${REPO_ROOT}"

# Step 3: Run local validation steps (outside act)
cd "${REPO_ROOT}"

run_step "Ruff lint" uv run ruff check src/ tests/

run_step "Ruff format check" uv run ruff format --check src/ tests/

run_step "Pyright type check" uv run pyright src/

run_step "Pytest" uv run pytest -v

run_step "Link validation" uv run python scripts/check_links.py

run_step "Package build" uv build

# Summary
echo -e "\n${YELLOW}━━━ Summary ━━━${RESET}"
for r in "${results[@]}"; do
    echo -e "  $r"
done
echo ""
echo -e "  ${GREEN}Passed: ${passed}${RESET}  ${RED}Failed: ${failed}${RESET}"

if [ "$failed" -gt 0 ]; then
    exit 1
fi
echo -e "\n${GREEN}All steps passed.${RESET}"
