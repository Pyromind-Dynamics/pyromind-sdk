#!/bin/bash
# Run all validation scripts
# This script runs all issue validation scripts and generates a summary report

set -e

REPORT_DIR="docs/validation"
REPORT_FILE="$REPORT_DIR/validation_report.md"

echo "PyroMind SDK Code Validation Report"
echo "Generated: $(date)"
echo ""
echo "Running validation scripts..."
echo ""

# Create report header
cat > "$REPORT_FILE" << 'EOF'
# PyroMind SDK Code Validation Report

**Generated:** $(date)
**Branch:** issue-validation-2026-03-27

## Summary

This report documents all code quality issues found during the code review.
Each issue has a validation script that reproduces the problem.

## Critical Issues

EOF

# Track issues
CRITICAL_COUNT=0
HIGH_COUNT=0
MEDIUM_COUNT=0

# Run each validation script
for script in "$REPORT_DIR"/*.py; do
    if [ -f "$script" ]; then
        script_name=$(basename "$script")
        echo "Running $script_name..."

        {
            echo ""
            echo "### $script_name"
            echo ""
            echo '```'
            python "$script" 2>&1 || true
            echo '```'
        } >> "$REPORT_FILE"

        # Count based on exit code
        if python "$script" >/dev/null 2>&1; then
            : # No issue
        else
            # Check severity from filename or content
            if grep -q "CRITICAL" "$script"; then
                ((CRITICAL_COUNT++))
            elif grep -q "HIGH" "$script"; then
                ((HIGH_COUNT++))
            else
                ((MEDIUM_COUNT++))
            fi
        fi
    fi
done

# Add summary
cat >> "$REPORT_FILE" << EOF

## Issue Counts

| Severity | Count |
|----------|-------|
| CRITICAL | $CRITICAL_COUNT |
| HIGH | $HIGH_COUNT |
| MEDIUM | $MEDIUM_COUNT |
| **TOTAL** | **$((CRITICAL_COUNT + HIGH_COUNT + MEDIUM_COUNT))** |

## Next Steps

1. Review this report
2. Create GitHub issues for each problem
3. Prioritize fixes based on severity
4. Apply fixes following the implementation plan

---

**End of Report**
EOF

echo ""
echo "Validation complete!"
echo "Report saved to: $REPORT_FILE"
echo ""
echo "Summary:"
echo "  Critical issues: $CRITICAL_COUNT"
echo "  High priority issues: $HIGH_COUNT"
echo "  Medium priority issues: $MEDIUM_COUNT"
echo "  Total: $((CRITICAL_COUNT + HIGH_COUNT + MEDIUM_COUNT))"
