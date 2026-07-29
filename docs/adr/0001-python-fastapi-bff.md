# Use Python and FastAPI for the BFF

`travel-web-api` will use Python 3.12 and FastAPI rather than
Node.js/TypeScript. Although the frontends use TypeScript, the project favors
the operator's existing Python deployment experience and the proven
SQLAlchemy/Alembic/PostgreSQL stack; frontend contract types can be generated
from OpenAPI, and the BFF remains isolated from `hermes-travel` behind HTTP.
