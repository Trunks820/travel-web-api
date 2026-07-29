# Linux.do L1 is a v0.2 alternative registration gate

v0.2 accepts an active, unsilenced Linux.do identity with trust level L1 or
higher without consuming an Invitation, using the provider's immutable user ID
as the identity subject. The L1 threshold applies only when creating a new User;
later L0 status does not lock an existing User out, while inactive or silenced
provider state blocks a new session. This trades invitation-level control for a
bounded community promotion channel without making email registration public.
