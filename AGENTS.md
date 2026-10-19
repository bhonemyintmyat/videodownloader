# VideoSaver — AI Coding Instructions

## Project Overview

VideoSaver is a web application for validating video URLs and identifying supported platforms. The initial MVP focuses on URL validation and provider detection.

## Technology Stack

- Frontend: React and JavaScript
- Backend: Python and FastAPI
- API data validation: Pydantic
- Testing: pytest

Use Node.js tooling for frontend development when required. Do not implement the backend in Node.js.

## Project Structure

- `frontend/`: React application and UI components.
- `backend/app/`: FastAPI application, URL validation, and provider detection.
- `backend/tests/`: Backend unit and API tests.
- `AGENTS.md`: Shared project instructions.

Follow the existing project structure where possible. Do not reorganize files unnecessarily.

## Coding Guidelines

- Write readable, maintainable Python and JavaScript.
- Use descriptive names and small, focused functions.
- Separate URL validation, provider detection, and API route handling.
- Use Pydantic models for API request and response validation.
- Handle invalid input and expected errors explicitly.
- Add or update tests for behavior changes.
- Avoid unnecessary dependencies and abstractions.

## URL Validation and Provider Detection

- Accept only absolute URLs using HTTP or HTTPS.
- Reject malformed URLs and unsupported schemes.
- Identify providers using parsed hostnames, not simple substring matching.
- Match known domains carefully, including approved subdomains.
- Return a clear result for unsupported or unrecognized providers.
- Treat provider detection as identification, not proof that a video is accessible.
- Do not fetch user-submitted URLs during the initial MVP.

## Security Requirements

- Never expose credentials, API keys, or environment secrets.
- Do not implement methods to bypass authentication, paywalls, DRM, or platform restrictions.
- Do not download protected or unauthorized platform media.
- Avoid server-side request forgery (SSRF) risks when handling user-provided URLs.
- Do not trust client-side validation alone; validate requests on the backend.
- Keep errors informative without revealing sensitive implementation details.

## Testing

- Use pytest for backend tests.
- Test valid URLs, malformed URLs, unsupported schemes, recognized providers, and unknown providers.
- Include tests for deceptive hostnames and URL parsing edge cases.
- Test API error responses as well as successful responses.
- Run the relevant tests after changing code and report any failures honestly.

## Development Workflow

- Inspect existing files before making changes.
- Keep changes focused on the requested task.
- Do not assume commands or dependencies exist; inspect the project configuration first.
- Document any new dependencies and setup requirements.
- Never claim that code or tests work unless they have been checked.
