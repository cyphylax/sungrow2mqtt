#!/bin/bash
# Script to bump version before push

# Read current version
VERSION_FILE="rootfs/app/modules/version.py"
CURRENT_VERSION=$(grep "__version__" $VERSION_FILE | sed 's/.*= "\(.*\)"/\1/')

# Bump version (simple increment last number)
IFS='.' read -r major minor patch <<< "$CURRENT_VERSION"
NEW_PATCH=$((patch + 1))
NEW_VERSION="$major.$minor.$NEW_PATCH"

# Update version file
sed -i "s/__version__ = \"$CURRENT_VERSION\"/__version__ = \"$NEW_VERSION\"/" $VERSION_FILE

# Update config.yaml
sed -i "s/version: \"$CURRENT_VERSION\"/version: \"$NEW_VERSION\"/" config.yaml

# Commit changes
git add $VERSION_FILE config.yaml
git commit -m "Bump version to $NEW_VERSION"

echo "Version bumped to $NEW_VERSION"