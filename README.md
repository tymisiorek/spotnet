# SpotNet

A 3D visualization of Spotify's collaboration network where each node represents an artist and edges show collaborations.

## Setup

### 1. Environment Variables

Create a `.env` file in the `backend/` directory with the following variables:

```env
# Spotify API Configuration
SPOTIFY_CLIENT_ID=your_spotify_client_id_here
SPOTIFY_CLIENT_SECRET=your_spotify_client_secret_here
SPOTIFY_REDIRECT_URI=http://localhost:8000/callback

# Flask Configuration
BACKEND_HOST=localhost
BACKEND_PORT=8000
APP_SECRET=your_random_secret_key_here

# Frontend Configuration (optional)
FRONTEND_ORIGIN=http://localhost:5173

# Development
FLASK_ENV=development
```

### 2. Spotify API Setup

1. Go to [Spotify Developer Dashboard](https://developer.spotify.com/dashboard)
2. Create a new application
3. Add `http://localhost:8000/callback` to the Redirect URIs
4. Copy the Client ID and Client Secret to your `.env` file

### 3. Running the Application

1. Start the backend:
   ```bash
   cd backend
   python main.py
   ```

2. Start the frontend:
   ```bash
   cd frontend
   npm install
   npm run dev
   ```

3. Visit `http://localhost:5173/network.html`

## Authentication Flow

The application now includes proper authentication handling:

- **Login Button**: Appears when user is not authenticated
- **Load Playlists Button**: Appears when user is authenticated
- **Logout Button**: Allows users to clear their session
- **Automatic Session Check**: Verifies authentication status on page load

## Troubleshooting

If you get an "unauthorized" error when loading playlists:

1. Make sure you have a valid `.env` file with Spotify credentials
2. Check that you're logged in with Spotify (click "Login with Spotify" if needed)
3. Verify your session hasn't expired (use the logout button to clear and re-login)