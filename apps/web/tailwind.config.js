/** @type {import('tailwindcss').Config} */
module.exports = {
  darkMode: ["class"],
  content: [
    "./app/**/*.{js,ts,jsx,tsx,mdx}",
    "./components/**/*.{js,ts,jsx,tsx,mdx}",
  ],
  theme: {
    extend: {
      colors: {
        chatgpt: {
          main: "#212121",
          sidebar: "#171717",
          input: "#2f2f2f",
          card: "#262626",
          cardHover: "#2d2d2d",
          border: "#333333",
          borderSubtle: "#282828",
          text: "#ececec",
          muted: "#b4b4b4",
          submuted: "#8e8e8e",
          green: "#10a37e",
        },
      },
    },
  },
  plugins: [],
}
