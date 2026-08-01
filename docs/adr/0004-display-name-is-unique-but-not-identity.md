# Display Name is globally unique but is not identity

v0.1.1 gives every User one mutable, globally unique Display Name so the hosted
product has a recognizable user-facing name without introducing username login,
public profiles, or a second identity system. Uniqueness is enforced on a
normalized PostgreSQL key, while authentication, authorization, ownership,
account linking, Administrator audit, and Hermes integration continue to use
immutable User or Login Identity identifiers; former names are quarantined for
15 days to prevent immediate reassignment.
