Terminal Generator
A robust Python-based utility designed to generate unique, formatted Terminal IDs (TID) for business applications. This tool ensures sequential ID generation using Base36 encoding and provides a web interface for easy management.

## Key Features
Sequential Base36 Encoding: Converts integer sequences into alphanumeric IDs to maximize ID space.

Standardized Formatting: Automatically pads IDs and applies a fixed prefix (e.g., 2ZN1) in full uppercase.

State Management: Utilizes MSSQL to track the last generated sequence, preventing duplicates.

User Interface: Built with Streamlit for a lightweight, interactive web dashboard.

Dockerized: Fully containerized for consistent deployment across environments.
