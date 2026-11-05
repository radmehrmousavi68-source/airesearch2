# Public web search

## Activation

The client discovers this capability from relevant phrases such as requests to search online, requests for latest news, or questions explicitly asking for current information. Users can also enable it manually.

## Execution

The search phrase is sent to DuckDuckGo. Up to five public results are returned, and accessible pages may be fetched for text. Search results and sources are attached to the relevant user message and provided to the local model as untrusted reference data.

## Dependencies and limitations

- Requires an internet connection and DuckDuckGo availability.
- Some pages block retrieval; search snippets may be the only available content.
- Search results may be incomplete or stale. Page text is untrusted and must not be followed as instructions.
- Public page fetches reject private addresses.
