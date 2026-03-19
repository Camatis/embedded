import React, { useState, useEffect } from 'react';
import './Dashboard.css';

function LandingPage({ onLogin, onCreateAccount }) {
  const [activeTab, setActiveTab] = useState('overview');
  const [aboutSection, setAboutSection] = useState('background');


  const renderAboutSection = () => {
    const cards = [
      {
        key: 'background',
        title: 'Background',
        icon: '🌱',
        text: 'The Carabao Mango is a high-value crop that contributes significantly to the Philippine economy. However, post-harvest sorting remains predominantly manual, relying on subjective visual inspection and the physical judgment of workers. This lack of standardization motivated the development of an automated size grading and defect classification system to ensure consistent, objective quality control.'
      },
      {
        key: 'hardware',
        title: 'Hardware',
        icon: '🔧',
        content: (
          <ul>
            <li>Automated Feeding: A hopper utilizes a servo-powered rotating gate to dispense mangoes one-by-one onto a motor-driven conveyor belt.</li>
            <li>Scanning Chamber: The core environment is illuminated by a high-CRI LED lighting system (5000-6500K) to ensure accurate, shadow-free image capture.</li>
            <li>IR Sensor Array: Three Infrared (IR) sensors are positioned at highly specific heights (8.5cm, 7.5cm, and 2cm) to instantly classify passing mangoes as large, medium, or small.</li>
            <li>Mechanical Routing: Based on the system's analysis, metal gear servo motors actuate sorting gates to physically direct the fruit into its corresponding bin.</li>
          </ul>
        )
      },
      {
        key: 'software',
        title: 'Software',
        icon: '🧠',
        content: (
          <ul>
            <li>Central Processing: The entire system acts as a localized edge node, controlled by a central Raspberry Pi 4 Model B.</li>
            <li>Computer Vision: The system relies on a YOLO26 Convolutional Neural Network (CNN) architecture to automatically learn and extract hierarchical features from raw images.</li>
            <li>Defect Detection: The AI model is specifically trained on a custom dataset to identify external surface abnormalities that compromise visual quality, such as scabs, bruises, and cuts.</li>
          </ul>
        )
      },
      {
        key: 'ui',
        title: 'User Interface',
        icon: '💻',
        content: (
          <ul>
            <li>Modern Stack: The dashboard is built using React.js for the frontend and Node.js for the backend.</li>
            <li>Live Monitoring: Operators can view a real-time camera feed to monitor the mangoes inside the chamber as they are processed.</li>
            <li>Data Logging: Every sorting session is recorded in the Batch History tab, allowing users to review the exact counts of small, medium, large, and defective yields.</li>
            <li>Hardware Diagnostics: A dedicated hardware status panel tracks the real-time detection states of the IR sensors and monitors the CPU temperature of the core device.</li>
          </ul>
        )
      }
    ];
    const current = cards.find(c => c.key === aboutSection);
    return (
      <div className="landing-tab-content">
        <div className="about-card-grid">
          {cards.map(c => (
            <div key={c.key} className={`about-card ${aboutSection === c.key ? 'active' : ''}`} onClick={() => setAboutSection(c.key)}>
              <div className="about-card-icon">{c.icon}</div>
              <h4>{c.title}</h4>
            </div>
          ))}
        </div>
        <div className="about-card-body" key={current.key}>
          <h3>{current.title}</h3>
          <div className="about-card-text">
            {current.content || <p>{current.text}</p>}
          </div>
        </div>
      </div>
    );
  };
  const renderTabContent = () => {
    if (activeTab === 'about') {
      return (
        <div className="landing-about landing-tab-panel">
          {renderAboutSection()}
        </div>
      );
    }

    if (activeTab === 'researchers') {
      return (
        <div className="landing-tab-content landing-tab-panel">
          <h3>Researchers</h3>
          <div className="researchers-grid">
            {[
              { name: 'Vince Camat', role: 'dadasdsadas', img: '/picture.png' },            //for roles and pictures
              { name: 'Aeriele Magbanua', role: 'sdsdfdsfdsf', img: '/picture.png' },
              { name: 'Shiloh Marfil', role: 'sgdfdgdgfgfd', img: '/picture.png' }
            ].map((person, idx) => (
              <div key={idx} className="researcher-card">
                <img src={person.img} alt={person.name} className="researcher-photo" />
                <h4>{person.name}</h4>
                <p>{person.role}</p>
              </div>
            ))}
          </div>
        </div>
      );
    }

    return (
      <div className="landing-tab-content landing-tab-panel overview-content">
        <h1 className="landing-main-title animate-title">MangoSort</h1>
        <h2 className="landing-subtitle animate-description">
          An IoT sensor-based size grading with advanced CNN computer vision to deliver consistent,
          objective, and efficient defect detection.
        </h2>
        <p className="landing-description">
          MangoSort is an automated cyber-physical prototype designed to streamline post-harvest processing
          for Philippine Carabao mangoes. Powered by a Raspberry Pi 4 Model B, it combines a motorized
          conveyor belt system with a YOLO26 deep learning model and an array of infrared (IR) sensors.
          As fruit passes through the controlled scanning chamber, the system instantly analyzes its size
          and surface quality, automatically routing it into designated bins for small, medium, large, or
          defective yields.
        </p>
      </div>
    );
  };

  return (
    <div className="landing-page">
      <header className="dashboard-header landing-header">
        <div className="header-content landing-header-content">
          <div className="landing-id">
            <h1 className="landing-logo-title">MangoSort</h1>
            
          </div>
          <nav className="landing-tabs">
            {['overview', 'about', 'researchers'].map(tab => (
              <button
                key={tab}
                className={`landing-tab ${activeTab === tab ? 'active' : ''}`}
                onClick={() => setActiveTab(tab)}
              >
                {tab.charAt(0).toUpperCase() + tab.slice(1)}
              </button>
            ))}
          </nav>
          <div className="landing-actions">
            <button className="landing-action-button" onClick={onCreateAccount}>Create Account</button>
            <button className="landing-action-button" onClick={onLogin}>Log In</button>
          </div>
        </div>
      </header>

      <div className="confetti" />
      <main className="landing-main">
        {renderTabContent()}
      </main>
      <footer className="landing-footer">© 2026 MangoSort - Built for Carabao Mango Quality Optimization</footer>
    </div>
  );
}

export default LandingPage;
