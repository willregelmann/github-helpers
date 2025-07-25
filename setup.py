from setuptools import setup

setup(
    name="gh-helpers",
    version="0.1.0",
    description="GitHub CLI helper tools for repository management",
    author="Will Regelmann",
    author_email="will@regelmann.net",
    py_modules=["gh_check_ahead", "gh_orphaned_prs", "gh_prune_branches", "github_utils"],
    install_requires=[
        # No external dependencies - uses GitHub CLI
    ],
    entry_points={
        'console_scripts': [
            'gh-check-ahead=gh_check_ahead:main',
            'gh-orphaned-prs=gh_orphaned_prs:main',
            'gh-prune-branches=gh_prune_branches:main',
        ],
    },
    python_requires=">=3.6",
)